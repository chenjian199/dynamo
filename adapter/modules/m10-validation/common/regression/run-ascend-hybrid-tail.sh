#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Continue the prepared r3 Ascend campaign after its fresh profile completes.
set -euo pipefail
cd "${1:?prepared remote M10 work directory}"
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
out=evidence/r3/final-tail
mkdir "$out"
trap 'echo $? > "$out/case.exit"' EXIT
profile=evidence/r3/offline-deepseek-20261104/case.exit
for ((i=0; i<2160; i++)); do
    [[ -f $profile ]] && break
    sleep 10
done
[[ $(cat "$profile") == 0 ]]
tools=m10-offline-r3-deepseek-tools
kubectl -n dynamo-m10 wait "pod/$tools" --for=condition=Ready --timeout=300s
failures=0

capture() {
    local name=$1 directory=$2
    kubectl -n dynamo-m10 get dgd "$name" -o json > "$directory/dgd.json"
    kubectl -n dynamo-m10 get pods -l "nvidia.com/dynamo-graph-deployment-name=$name" -o json > "$directory/pods.json"
    local pod
    while read -r pod; do
        kubectl -n dynamo-m10 logs "$pod" --all-containers > "$directory/$pod.log" 2>&1 || true
    done < <(python3 -c 'import json,sys; print("\n".join(x["metadata"]["name"] for x in json.load(open(sys.argv[1]))["items"]))' "$directory/pods.json")
}
cleanup() {
    local name=$1
    kubectl -n dynamo-m10 delete dgd "$name" --wait=true --timeout=300s
    kubectl -n dynamo-m10 wait --for=delete pod -l "nvidia.com/dynamo-graph-deployment-name=$name" --timeout=300s
}

if [[ -f evidence/r3/hybrid-aggregate/case.exit ]]; then
    if ! python3 - <<'PY_CHECK'
import json
from pathlib import Path
p=Path('evidence/r3/hybrid-aggregate')
assert (p/'case.exit').read_text().strip()=='0'
assert json.loads((p/'client.json').read_text())['passed'] is True
assert json.loads((p/'parity.json').read_text())['passed'] is True
PY_CHECK
    then failures=$((failures+1)); fi
else
name=m10-hybrid-aggregate-r3
directory=evidence/r3/hybrid-aggregate
mkdir "$directory"
kubectl apply -f manifests-r3/hybrid-aggregate.json
ready=false
for ((i=0; i<480; i++)); do
    kubectl -n dynamo-m10 get pods -l "nvidia.com/dynamo-graph-deployment-name=$name" -o json > "$directory/latest-pods.json"
    if python3 - "$directory/latest-pods.json" <<'PY'
import json,sys
pods=json.load(open(sys.argv[1]))['items']
assert len(pods)==2
assert all(any(c['type']=='Ready' and c['status']=='True' for c in p.get('status',{}).get('conditions',[])) for p in pods)
PY
    then ready=true; break; fi
    sleep 5
done
if "$ready" && kubectl -n dynamo-m10 exec "$tools" -- /opt/dynamo/tools-venv/bin/python /offline/hybrid-pd-client.py \
    --url "http://$name-frontend:8000" --load-script /offline/test-drivers/adapter/modules/m09-telemetry-planner/common/online/load.py \
    --output "/offline/$directory/client.json" > "$directory/client.log" 2>&1; then
    echo 0 > "$directory/case.exit"
    python3 compare-hybrid-outputs.py --aggregate "$directory/client.json" \
        --pd evidence/r3/hybrid-deepseek-token-parity/client.json --output "$directory/parity.json" \
        > "$directory/parity.log" 2>&1 || failures=$((failures+1))
else
    echo 1 > "$directory/case.exit"
    failures=$((failures+1))
fi
capture "$name" "$directory"
cleanup "$name"

fi

directory=evidence/r3/planner-deepseek-bounded-20261104
mkdir "$directory"
cat > "$directory/followup.json" <<'JSON'
{"reason":"The original OSL512 calibration exceeded the 180s client timeout. Independent native profiling measured about 223s for 500 output tokens. Preserve that failure and separately validate bounded OSL128 functional scaling.","concurrency":16,"isl":1024,"osl":128,"duration":900,"recovery":120,"request_timeout":180,"target_policy":"Unchanged single-request calibration formula; no production SLA claim","source_overlay":false}
JSON
kubectl apply -f manifests-r3/planner-deepseek.json
if python3 test-drivers/adapter/modules/m09-telemetry-planner/common/online/campaign.py --namespace dynamo-m10 --name m10-deepseek \
    --tools-pod "$tools" --output "$directory/online" --load-script test-drivers/adapter/modules/m09-telemetry-planner/common/online/load.py \
    --duration 900 --recovery 120 --concurrency 16 --isl 1024 --osl 128 --request-timeout 180 --token-id-metrics \
    > "$directory/campaign.log" 2>&1; then
    echo 0 > "$directory/case.exit"
else
    echo 1 > "$directory/case.exit"
    failures=$((failures+1))
fi
capture m10-deepseek "$directory"
cleanup m10-deepseek

# The original two-device Job preserved a failed colocated-PD fixture. ADXL
# explicitly rejects self-device connections. Its hardware/advanced results
# remain independent; the corrected PD Job uses five distinct NPUs.
for name in m10-final-r3 m10-pd-r3-distinct; do
    directory=evidence/r3/final-job
    if [[ $name == m10-pd-r3-distinct ]]; then directory=evidence/r3/pd-distinct-job; fi
    mkdir "$directory"
    complete=false
    for ((i=0; i<1100; i++)); do
        kubectl -n dynamo-m10 get job "$name" -o json > "$directory/job.json"
        state=$(python3 -c 'import json,sys; s=json.load(open(sys.argv[1])).get("status",{}); print("passed" if s.get("succeeded") else "failed" if any(c["type"]=="Failed" and c["status"]=="True" for c in s.get("conditions",[])) else "running")' "$directory/job.json")
        if [[ $state == passed ]]; then complete=true; break; fi
        [[ $state == failed ]] && break
        sleep 10
    done
    kubectl -n dynamo-m10 get pods -l "job-name=$name" -o json > "$directory/pods.json"
    kubectl -n dynamo-m10 logs "job/$name" > "$directory/job.log" 2>&1 || true
    if "$complete"; then echo 0 > "$directory/case.exit"; else echo 1 > "$directory/case.exit"; fi
    if [[ $name == m10-pd-r3-distinct ]] && ! "$complete"; then failures=$((failures+1)); fi
done
if ! python3 - <<'PY'
import json
from pathlib import Path
root=Path('evidence/r3')
for relative,count in [('hardware',6),('advanced-final',2),('pd-distinct',3)]:
    rows=(root/relative/'status.tsv').read_text().splitlines()
    assert len(rows)==count and all(x.endswith('\tpassed') for x in rows),relative
assert json.loads((root/'pd-distinct/parity.json').read_text())['passed'] is True
if (root/'final-job/case.exit').read_text().strip()!='0':
    assert json.loads((root/'pd/diagnosis.json').read_text())['status']=='configuration_failure'
    assert (root/'pd/status.tsv').read_text().splitlines()==['aggregate\tpassed','pd-2p3d\tfailed']
PY
then failures=$((failures+1)); fi
test "$failures" -eq 0
