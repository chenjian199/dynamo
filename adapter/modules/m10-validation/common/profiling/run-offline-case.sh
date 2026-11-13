#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run from the prepared remote work directory. Only test drivers are mounted.
set -euo pipefail
runtime_version=${M10_RUNTIME_VERSION:?set to the measured installed Dynamo version}
platform=${1:?cuda or ascend}
model=${2:?qwen32 or deepseek}
case "$platform" in
  cuda) py=/opt/dynamo/venv/bin/python ;;
  ascend) export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; py=/opt/dynamo/tools-venv/bin/python ;;
  *) exit 2 ;;
esac
case "$model" in qwen32|deepseek) ;; *) exit 2 ;; esac
revision=${M10_REVISION:-r1}
[[ $revision =~ ^r[0-9]+$ ]]
input="offline-large-$revision/$model"
out="evidence/$revision/offline-$model-20261104"
name="m10-offline-$revision-$model"
pod="$name-tools"
mkdir -p "$out/snapshots"
trap 'echo $? > "$out/case.exit"' EXIT
test ! -e "$out/profile"
kubectl apply -f "$input/rbac.json" -f "$input/tools.json"
kubectl -n dynamo-m10 wait "pod/$pod" --for=condition=Ready --timeout=300s
kubectl -n dynamo-m10 exec "$pod" -- "$py" "/offline/offline-$revision/offline-profile.py" \
  --input "/offline/$input/input.json" --output "/offline/$out/profile" > "$out/measure.log" 2>&1 &
measure=$!
while kill -0 "$measure" 2>/dev/null; do
  stamp=$(date -u +%Y%m%dT%H%M%S)
  kubectl -n dynamo-m10 get dynamoworkermetadata -o json > "$out/snapshots/$stamp.mdc.json"
  kubectl -n dynamo-m10 get pods -o json > "$out/snapshots/$stamp.pods.json"
  sleep 15
done
wait "$measure"
python3 audit-profile.py --profile "$out/profile" --snapshots "$out/snapshots" \
  --log "$out/measure.log" --output "$out/audit"
kubectl -n dynamo-m10 exec "$pod" -- "$py" /offline/check_offline_profile.py \
  --profile "/offline/$out/profile" --output "/offline/$out/audit/bootstrap.json" \
  --mdc "/offline/$out/audit/decode-metadata.json" \
  --prefill-mdc "/offline/$out/audit/prefill-metadata.json" > "$out/audit/bootstrap.log" 2>&1
# An unavailable native model is a failed predictive check, never forced ready.
kubectl -n dynamo-m10 exec "$pod" -- "$py" "/offline/offline-$revision/offline-profile.py" \
  --input "/offline/$input/input.json" --output "/offline/$out/profile" --reuse-profile
python3 prepare-online.py --runtime-version "$runtime_version" --profile "$out/profile" --input "$input/input.json" --output "$out/online.yaml"
kubectl apply -f "$out/online.yaml"
extra=()
if [[ $model == deepseek ]]; then extra=(--token-id-metrics); fi
python3 test-drivers/adapter/modules/m09-telemetry-planner/common/online/campaign.py --namespace dynamo-m10 --name "$name" \
  --tools-pod "$pod" --output "$out/online" --load-script test-drivers/adapter/modules/m09-telemetry-planner/common/online/load.py \
  --duration 300 --recovery 120 --concurrency 2 --isl 512 --osl 128 \
  --minimum-peak-ready 1 --disagg "${extra[@]}"
python3 restart-profile.py --namespace dynamo-m10 --name "$name" --output "$out/restart"
kubectl -n dynamo-m10 delete dgd "$name" --wait=true --timeout=300s
kubectl -n dynamo-m10 wait --for=delete pod -l "nvidia.com/dynamo-graph-deployment-name=$name" --timeout=300s
fingerprint=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$out/profile/profile-identity.json")
python3 check_online_planning.py --planner "$out/online/planner.log" --load "$out/online/load.log" \
  --fingerprint "$fingerprint" --output "$out/audit/online.json"
