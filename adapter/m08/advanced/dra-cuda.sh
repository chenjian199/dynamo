#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run on worker06, with the driver image already imported on worker28.
set -euo pipefail
cd -- "${1:?Specify the M08 working directory}"
out=evidence-advanced/dra
mkdir -p "$out"
kubectl get node worker28 -o json > "$out/node-before.json"
kubectl get pods -A --field-selector spec.nodeName=worker28 -o json > "$out/pods-before.json"
python3 - "$out" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
node = json.loads((root / "node-before.json").read_text())
assert node["metadata"]["labels"]["nvidia.com/gpu.deploy.device-plugin"] == "true"
assert not any(t["key"] == "heyi.io/m08-dra" for t in node["spec"].get("taints", []))
for pod in json.loads((root / "pods-before.json").read_text())["items"]:
    if pod["status"]["phase"] in ["Succeeded", "Failed"]:
        continue
    assert not pod["spec"].get("resourceClaims"), pod["metadata"]["name"]
    for container in pod["spec"]["containers"] + pod["spec"].get("initContainers", []):
        assert not any(k.startswith("nvidia.com/") for k in container.get("resources", {}).get("limits", {})), pod["metadata"]["name"]
PY
# Restore scalar allocation only after DRA Pods and their driver have exited.
restore() {
    rc=$?
    trap - EXIT
    kubectl -n dynamo-m08-1029 delete dgd m08-dra --ignore-not-found --wait=true --timeout=180s
    kubectl -n dynamo-m08-1029 wait --for=delete pod -l nvidia.com/dynamo-graph-deployment-name=m08-dra --timeout=180s || true
    if kubectl -n dynamo-m08-1029 get pods -o name | grep -q '^pod/m08-dra-'; then
        echo 'DRA workload still present; leave Device Plugin disabled until cleanup completes' >&2
        exit 1
    fi
    kubectl -n dynamo-m08-1029 get resourceclaims -o json > "$out/claims-after-delete.json"
    kubectl -n dynamo-m08-1029 delete resourceclaimtemplate m08-gpu --ignore-not-found
    if helm status heyi-dra -n dra-m08 >/dev/null 2>&1; then
        helm uninstall heyi-dra -n dra-m08 --wait --timeout 3m
    fi
    for attempt in $(seq 1 90); do
        count=$(kubectl -n dra-m08 get pods -o name | wc -l)
        [[ "$count" == 0 ]] && break
        sleep 2
    done
    [[ "$count" == 0 ]]
    kubectl label node worker28 nvidia.com/gpu.deploy.device-plugin=true --overwrite
    kubectl taint node worker28 heyi.io/m08-dra:NoSchedule-
    kubectl get node worker28 -o json > "$out/node-restored.json"
    exit "$rc"
}
trap restore EXIT
kubectl taint node worker28 heyi.io/m08-dra=true:NoSchedule
kubectl label node worker28 nvidia.com/gpu.deploy.device-plugin=false --overwrite
for attempt in $(seq 1 60); do
    count=$(kubectl -n gpu-operator get pods --field-selector spec.nodeName=worker28 -o name | grep -c nvidia-device-plugin || true)
    [[ "$count" == 0 ]] && break
    sleep 2
done
[[ "$count" == 0 ]]
helm upgrade --install heyi-dra dra-driver-nvidia-gpu-0.5.1.tgz -n dra-m08 --create-namespace -f dra-values.json --wait --timeout 5m > "$out/install.log" 2>&1
kubectl get deviceclasses,resourceslices -o json > "$out/inventory.json"
kubectl apply -f dra-claim-template.json
kubectl apply --dry-run=server -f dra.json > "$out/admission.log" 2>&1
kubectl apply -f dra.json
python3 - "$out" <<'PY'
import json, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
deadline = time.monotonic() + 900
while time.monotonic() < deadline:
    pods = json.loads(subprocess.check_output(["kubectl", "-n", "dynamo-m08-1029", "get", "pods", "-o", "json"]))
    workers = [p for p in pods["items"] if p["metadata"]["name"].startswith("m08-dra-worker-")]
    if len(workers) == 2 and all(any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", [])) for p in workers):
        break
    time.sleep(5)
else:
    raise TimeoutError("DRA workers not ready")
for pod in workers:
    assert pod["spec"]["nodeName"] == "worker28"
    assert pod["spec"]["resourceClaims"]
    name = pod["metadata"]["name"]
    result = subprocess.check_output(["kubectl", "-n", "dynamo-m08-1029", "exec", name, "-c", "main", "--", "python", "-c", "import torch; assert torch.cuda.device_count() == 1, torch.cuda.device_count(); print(torch.cuda.get_device_name(0))"], timeout=60)
    (root / (name + ".device.txt")).write_bytes(result)
(root / "pods-initial.json").write_text(json.dumps(pods, indent=2))
(root / "claims-initial.json").write_bytes(subprocess.check_output(["kubectl", "-n", "dynamo-m08-1029", "get", "resourceclaims", "-o", "json"]))
PY
python3 regress.py --name m08-dra --client source/adapter/m02/cluster/client.py --output "$out/regression"
kubectl -n dynamo-m08-1029 get resourceclaims -o json > "$out/claims-after-scale-zero.json"
kubectl -n dra-m08 logs -l app.kubernetes.io/instance=heyi-dra --all-containers --prefix > "$out/driver.log" 2>&1 || true
