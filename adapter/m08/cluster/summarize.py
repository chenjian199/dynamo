#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate collected M08 evidence and write a compact, reproducible summary."""

import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--results", type=Path, default=Path(__file__).parent / "results")
a = p.parse_args()
report = {
    "date": "2026-10-29",
    "scope": "Device Plugin / component DGD / Kubernetes discovery",
    "model": "Qwen3-0.6B",
    "platforms": {},
    "not_certified": [
        "MetaX Kubernetes cluster",
        "cross-node Operator PD or device-direct RDMA",
        "Grove, DGDR automatic profiling, vendor DRA or shared devices",
        "GMS/failover or M09 Planner",
    ],
}
for platform, version in [("cuda", "0.28.0"), ("ascend", "0.23.0+empty")]:
    root = a.results / platform

    def read(name):
        return json.loads((root / name).read_text())

    versions = read("runtime-versions.json")
    assert versions["vllm"] == version
    admission = read("admission.json")
    assert len(admission) == 6 and all(c["passed"] for c in admission)
    for mode in ["aggregate", "pd"]:
        assert read(mode + "/summary.json")["passed"], (platform, mode)
        before = read(mode + "/before-recreate.pods.json")["items"]
        after = read(mode + "/after-recreate.pods.json")["items"]
        assert {p["metadata"]["uid"] for p in before} != {
            p["metadata"]["uid"] for p in after
        }
    zero = read("aggregate/scale-zero.dgd.json")["status"]["components"]["worker"]
    assert zero["replicas"] == zero["readyReplicas"] == 0
    assert zero["gpusPerEngine"] == zero["gpusPerReplica"] == 1
    pairs = read("pd/pairs.json")
    assert pairs["passed"] and len(pairs["pairs"]) == 6
    assert len({x["prefill"] for x in pairs["pairs"]}) == 2
    assert len({x["decode"] for x in pairs["pairs"]}) == 3
    assert len({(x["prefill"], x["decode"]) for x in pairs["pairs"]}) == 6
    assert len({tuple(x["token_ids"]) for x in pairs["pairs"]}) == 1
    cross = read("cross/summary.json")
    assert cross["passed"] and len(set(cross["nodes"])) == 2
    api_paths = [
        "aggregate/" + s + "-api.json"
        for s in ["initial", "scale-3", "scale-2", "recovery"]
    ] + ["pd/initial-api.json", "pd/recovery-api.json", "cross/summary.json"]
    cancellations = 0
    kv_rates = {}
    for name in api_paths:
        result = read(name)
        assert result["passed"] and result["sse_parity"]
        assert result["post_cancel_recovery"] and result["cancellations"] == 4
        cancellations += result["cancellations"]
        if not name.startswith("pd/"):
            rate = max(
                x["nvext"]["timing"]["kv_hit_rate"] for x in result["long_prefix"]
            )
            assert rate > 0
            kv_rates[name] = rate
    cleanup = read("cleanup.json")
    assert cleanup["passed"]
    assert not cleanup["resources"]["dgd"]["items"]
    assert not cleanup["resources"]["dynamoworkermetadatas"]["items"]
    assert cleanup["operator"]["status"]["readyReplicas"] == 1
    assert len(cleanup["resources"]["pods"]["items"]) == 1
    image_ids = sorted(
        {
            c["imageID"]
            for pod in read("pd/initial.pods.json")["items"]
            for c in pod["status"]["containerStatuses"]
        }
    )
    report["platforms"][platform] = {
        "passed": True,
        "versions": versions,
        "runtime_image_ids": image_ids,
        "admission_cases": 6,
        "aggregate_replicas": [2, 3, 2, 0],
        "worker_and_decode_recreation": True,
        "pd_handoff_pairs": 6,
        "cross_node_aggregate_nodes": cross["nodes"],
        "api_suites": len(api_paths),
        "cancellations_with_recovery": cancellations,
        "router_kv_hit_rates": kv_rates,
        "cleanup": "Only Operator remains; no test DGD or WorkerMetadata",
        "first_run_note": "Original zero-replica status race retained in aggregate-first-run; aggregate is the complete rerun.",
    }
report["passed"] = all(r["passed"] for r in report["platforms"].values())
report["evidence_sha256"] = {
    str(f.relative_to(a.results)): hashlib.sha256(f.read_bytes()).hexdigest()
    for platform in report["platforms"]
    for f in sorted((a.results / platform).rglob("*"))
    if f.is_file() and not f.name.endswith(".license")
}
(a.results / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
print("M08 evidence validated for CUDA and Ascend")
