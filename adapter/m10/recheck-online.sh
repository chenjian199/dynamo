#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run from a prepared M10 cluster work directory, after fixing metrics selectors.
set -euo pipefail
platform=${1:?cuda or ascend}
model=${2:?small, qwen32 or deepseek}
case "$platform" in cuda) ;; ascend) export KUBECONFIG=/etc/rancher/k3s/k3s.yaml ;; *) exit 2 ;; esac
case "$model" in
 small) profile=evidence/r1/offline-small; input=offline-r1/input.json; name=m10-offline-r1 ;;
 qwen32|deepseek) profile="evidence/r1/offline-$model-20261104/profile"; input="offline-large-r1/$model/input.json"; name="m10-offline-r1-$model" ;;
 *) exit 2 ;;
esac
out="evidence/r1/metrics-recheck-$model-20261104"
mkdir "$out"
trap 'echo $? > "$out/case.exit"' EXIT
python3 prepare-online.py --profile "$profile" --input "$input" --output "$out/online.yaml"
kubectl apply -f "$out/online.yaml"
extra=()
[[ $model == deepseek ]] && extra=(--token-id-metrics)
python3 test-drivers/adapter/m09/campaign.py --namespace dynamo-m10 --name "$name" \
 --tools-pod m10-offline-r1-tools --output "$out/online" \
 --load-script test-drivers/adapter/m09/load.py --duration 300 --recovery 120 \
 --concurrency 2 --isl 512 --osl 128 --minimum-peak-ready 1 --disagg "${extra[@]}"
python3 restart-profile.py --namespace dynamo-m10 --name "$name" --output "$out/restart"
kubectl -n dynamo-m10 delete dgd "$name" --wait=true --timeout=300s
kubectl -n dynamo-m10 wait --for=delete pod -l "nvidia.com/dynamo-graph-deployment-name=$name" --timeout=300s
fingerprint=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["fingerprint"])' "$profile/profile-identity.json")
python3 check_online_planning.py --planner "$out/online/planner.log" --load "$out/online/load.log" \
 --fingerprint "$fingerprint" --output "$out/planning-audit.json"
