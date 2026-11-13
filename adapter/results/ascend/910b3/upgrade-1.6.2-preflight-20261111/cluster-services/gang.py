#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prove a two-worker gang waits with one free device, then runs after release."""

import argparse
import json
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--manifest", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
dgd = json.loads(a.manifest.read_text())
namespace = dgd["metadata"]["namespace"]
name = dgd["metadata"]["name"]
a.output.mkdir(parents=True, exist_ok=True)
worker = next(c for c in dgd["spec"]["components"] if c.get("type") == "worker")
spec = worker["podTemplate"]["spec"]
node = spec["nodeSelector"].get("kubernetes.io/hostname")
if not node:
    p.error(
        "Gang barrier needs a manifest pinned to one dedicated node; it cannot prove a barrier across an unrestricted cluster"
    )
resource = next(
    r
    for r in spec["containers"][0]["resources"]["limits"]
    if r in ["nvidia.com/gpu", "huawei.com/Ascend910", "metax-tech.com/gpu"]
)


def kube(*args, data=None):
    return subprocess.check_output(
        ["kubectl", "-n", namespace, *args], input=data, timeout=180
    )


node_data = json.loads(kube("get", "node", node, "-o", "json"))
capacity = int(node_data["status"]["allocatable"][resource])
assert capacity >= 2
pods = json.loads(kube("get", "pods", "-A", "-o", "json"))
for pod in pods["items"]:
    if pod["spec"].get("nodeName") == node and pod["status"]["phase"] not in [
        "Succeeded",
        "Failed",
    ]:
        for container in pod["spec"]["containers"]:
            assert not int(
                container.get("resources", {}).get("limits", {}).get(resource, 0)
            ), pod["metadata"]["name"]
hold_name = name + "-capacity-hold"
hold = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {"name": hold_name, "namespace": namespace},
    "spec": {
        "nodeSelector": spec["nodeSelector"],
        "restartPolicy": "Never",
        "activeDeadlineSeconds": 900,
        "containers": [
            {
                "name": "hold",
                "image": spec["containers"][0]["image"],
                "imagePullPolicy": "Never",
                "command": ["/bin/sh", "-c", "sleep 900"],
                "resources": {"limits": {resource: capacity - 1}},
            }
        ],
    },
}
if "runtimeClassName" in spec:
    hold["spec"]["runtimeClassName"] = spec["runtimeClassName"]
(a.output / "hold.json").write_text(json.dumps(hold, indent=2))
report = {"passed": False, "node": node, "capacity": capacity, "held": capacity - 1}
try:
    kube("apply", "-f", "-", data=json.dumps(hold).encode())
    kube("wait", "--for=condition=Ready", "pod/" + hold_name, "--timeout=120s")
    kube("apply", "-f", str(a.manifest))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        pods = json.loads(kube("get", "pods", "-o", "json"))
        workers = [
            p
            for p in pods["items"]
            if p["metadata"]["name"].startswith(
                name + "-0-" + worker["name"].lower() + "-"
            )
        ]
        if len(workers) == 2:
            break
        time.sleep(3)
    assert len(workers) == 2
    for sample in range(6):
        pods = json.loads(kube("get", "pods", "-o", "json"))
        workers = [
            p
            for p in pods["items"]
            if p["metadata"]["name"].startswith(
                name + "-0-" + worker["name"].lower() + "-"
            )
        ]
        assert len(workers) == 2
        assert all(p["spec"]["schedulerName"] == "kai-scheduler" for p in workers)
        assert all(
            not p["spec"].get("nodeName") for p in workers
        ), "Gang partially scheduled despite insufficient capacity"
        (a.output / f"blocked-{sample}.pods.json").write_text(
            json.dumps(pods, indent=2)
        )
        time.sleep(5)
    for kind in ["podgroups.scheduling.run.ai", "podcliquesets", "events"]:
        (a.output / (kind + ".json")).write_bytes(kube("get", kind, "-o", "json"))
    report["passed"] = True
finally:
    kube(
        "delete",
        "pod",
        hold_name,
        "--ignore-not-found",
        "--wait=true",
        "--timeout=120s",
    )
    (a.output / "barrier.json").write_text(json.dumps(report, indent=2))
print("Gang barrier verified; capacity released for the real inference regression")
