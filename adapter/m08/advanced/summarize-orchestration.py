#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate actual Grove/KAI and NVIDIA DRA evidence independently of DGDR."""

import json
from pathlib import Path

root = Path(__file__).parent / "results"
report = {
    "grove": {},
    "nvidia_dra": {},
    "ascend_dra": "awaiting Kubernetes >=1.34 test cluster",
}


def read(platform, path):
    return json.loads((root / platform / path).read_text())


def regression(platform, path):
    assert read(platform, path + "/summary.json")["passed"]
    for stage in ["initial", "scale-3", "scale-2", "recovery"]:
        api = read(platform, path + f"/{stage}-api.json")
        assert api["passed"] and api["sse_parity"]
        assert api["cancellations"] == 4 and api["post_cancel_recovery"]
    before = read(platform, path + "/before-recreate.pods.json")["items"]
    after = read(platform, path + "/after-recreate.pods.json")["items"]
    assert {p["metadata"]["uid"] for p in before} != {
        p["metadata"]["uid"] for p in after
    }
    zero = read(platform, path + "/scale-zero.dgd.json")
    assert zero["status"]["components"]["worker"]["replicas"] == 0
    assert zero["status"]["components"]["worker"]["readyReplicas"] == 0


for platform in ["cuda", "ascend"]:
    regression(platform, "grove")
    regression(platform, "grove-kai/regression")
    barrier = read(platform, "grove-kai/gang/barrier.json")
    assert barrier["passed"] and barrier["capacity"] - barrier["held"] == 1
    for index in range(6):
        pods = read(platform, f"grove-kai/gang/blocked-{index}.pods.json")["items"]
        workers = [
            p
            for p in pods
            if "m08-grove-kai" in p["metadata"]["name"]
            and "worker" in p["metadata"]["name"]
        ]
        assert len(workers) == 2
        assert all(
            p["spec"]["schedulerName"] == "kai-scheduler"
            and not p["spec"].get("nodeName")
            for p in workers
        )
    for suffix in ["grove", "grove-kai"]:
        cleanup = read(platform, suffix + "-cleanup.json")
        assert (
            cleanup["passed"]
            and not cleanup["resources"]["dynamoworkermetadatas"]["items"]
        )
    report["grove"][platform] = {
        "default_scheduler": "passed",
        "kai_gang_barrier": "passed",
        "kai_scaling_recreation_api": "passed",
        "cancellations_per_scheduler": 16,
        "cleanup": "passed",
    }

regression("cuda", "dra/regression")
claims = read("cuda", "dra/claims-initial.json")["items"]
assert len(claims) == 2
allocated = [c["status"]["allocation"]["devices"]["results"] for c in claims]
assert all(len(devices) == 1 for devices in allocated)
assert all(devices[0]["driver"] == "gpu.nvidia.com" for devices in allocated)
assert len({(d[0]["pool"], d[0]["device"]) for d in allocated}) == 2
assert not read("cuda", "dra/claims-after-delete.json")["items"]
node = read("cuda", "dra/node-restored.json")
assert node["metadata"]["labels"]["nvidia.com/gpu.deploy.device-plugin"] == "true"
assert not any(t["key"] == "heyi.io/m08-dra" for t in node["spec"].get("taints", []))
report["nvidia_dra"] = {
    "distinct_device_claims": 2,
    "scaling_recreation_api": "passed",
    "cancellations": 16,
    "claims_after_dgd_deletion": 0,
    "device_plugin_restored": True,
}
report["not_certified"] = [
    "DGDR (separate evidence required)",
    "Ascend DRA",
    "MetaX Kubernetes",
    "MIG/shared DRA allocations",
    "KAI topology-aware multi-node gangs",
]
(root / "orchestration-summary.json").write_text(json.dumps(report, indent=2) + "\n")
print("Grove/KAI on CUDA and Ascend, and NVIDIA DRA evidence validated")
