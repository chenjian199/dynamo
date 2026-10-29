#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check the expanded M08 cross-node PD evidence without certifying other paths."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).parent / "results"
report = {"scope": "Operator cross-node 2P3D", "platforms": {}}
for platform in ["cuda", "ascend"]:
    directory = root / platform

    def read(name):
        return json.loads((directory / name).read_text())

    assert read("pd-cross/summary.json")["passed"]
    roles = {}
    pods = read("pd-cross/initial.pods.json")["items"]
    for role, count in [("prefill", 2), ("decode", 3)]:
        selected = [p for p in pods if f"-{role}-" in p["metadata"]["name"]]
        assert len(selected) == count
        nodes = {p["spec"]["nodeName"] for p in selected}
        assert len(nodes) == 1
        roles[role] = next(iter(nodes))
    assert roles["prefill"] != roles["decode"]
    pairs = read("pd-cross/pairs.json")
    assert pairs["passed"] and len(pairs["pairs"]) == 6
    assert len({p["prefill"] for p in pairs["pairs"]}) == 2
    assert len({p["decode"] for p in pairs["pairs"]}) == 3
    assert len({(p["prefill"], p["decode"]) for p in pairs["pairs"]}) == 6
    assert len({tuple(p["token_ids"]) for p in pairs["pairs"]}) == 1
    before = read("pd-cross/before-recreate.pods.json")["items"]
    after = read("pd-cross/after-recreate.pods.json")["items"]
    assert {p["metadata"]["uid"] for p in before} != {
        p["metadata"]["uid"] for p in after
    }
    for stage in ["initial", "recovery"]:
        api = read(f"pd-cross/{stage}-api.json")
        assert api["passed"] and api["sse_parity"]
        assert api["cancellations"] == 4 and api["post_cancel_recovery"]
    cleanup = read("pd-cross-cleanup.json")
    assert cleanup["passed"]
    assert not cleanup["resources"]["dgd"]["items"]
    assert not cleanup["resources"]["dynamoworkermetadatas"]["items"]
    report["platforms"][platform] = {
        "passed": True,
        "nodes_by_role": roles,
        "handoff_pairs": 6,
        "cancellations_with_recovery": 8,
        "decode_recreation": True,
        "cleanup": True,
    }
report["not_certified_by_this_report"] = [
    "CUDA GPUDirect/RDMA (this run uses TCP and CUDA copy)",
    "Grove/KAI gang scheduling",
    "DGDR profiling and auto-deployment",
    "NVIDIA DRA",
    "Ascend HAMi DRA: awaiting user-provided Kubernetes >=1.34 cluster",
    "MetaX Kubernetes: no test cluster",
]
report["evidence_sha256"] = {
    str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
    for platform in report["platforms"]
    for p in sorted((root / platform).rglob("*"))
    if p.is_file() and not p.name.endswith(".license")
}
(root / "pd-cross-summary.json").write_text(json.dumps(report, indent=2) + "\n")
print("CUDA and Ascend cross-node Operator 2P3D evidence validated")
