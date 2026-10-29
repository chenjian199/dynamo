#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check successful real DGDR outputs and every AIPerf run, not only exit codes."""

import json
from collections import Counter
from pathlib import Path

root = Path(__file__).parent / "results"
report = {}
for platform in ("cuda", "ascend"):
    base = root / platform / "dgdr"
    summary = json.loads((base / "summary.json").read_text())
    dgdr = json.loads((base / "dgdr.json").read_text())
    dgd = json.loads((base / "dgd.json").read_text())
    archive = json.loads((base / "archive.json").read_text())
    pods = json.loads((base / "pods.json").read_text())["items"]
    baseline = json.loads(
        (root.parent.parent / "cluster/results/summary.json").read_text()
    )["platforms"][platform]
    image_ids = {
        c["imageID"]
        for pod in pods
        for c in pod["status"]["containerStatuses"]
        if c["name"] == "main"
    }
    assert image_ids == set(baseline["runtime_image_ids"]), image_ids
    assert summary["passed"] and summary["auto_apply"]
    assert summary["cancellations"] == 4
    assert dgdr["status"]["phase"] == "Deployed"
    assert dgdr["status"]["dgdName"] == dgd["metadata"]["name"]
    assert archive["bytes"] > 0 and len(archive["sha256"]) == 64
    exports = sorted((base / "profiler").rglob("profile_export_aiperf.json"))
    # AIPerf also exports phase copies and Profiler runs a separate decode
    # warmup campaign at each load. Do not count those twice as measurements.
    profiles = [
        p
        for p in exports
        if "phases" not in p.relative_to(base / "profiler").parts
        and not p.parent.name.endswith("_warmup")
    ]
    warmups = [
        p
        for p in exports
        if "phases" not in p.relative_to(base / "profiler").parts
        and p.parent.name.endswith("_warmup")
    ]
    assert len(warmups) == 12, (platform, len(warmups))
    assert len(profiles) == 14, (platform, len(profiles))
    groups = Counter()
    requests = 0
    for path in profiles + warmups:
        data = json.loads(path.read_text())
        assert data["is_complete"] and not data["was_cancelled"], path
        assert not data["error_summary"], path
        assert data["request_error_rate"]["avg"] == 0, path
        completed = data["completed_request_count"]["avg"]
        assert completed > 0 and completed == data["request_count"]["avg"], path
        if path in profiles:
            groups[path.relative_to(base / "profiler").parts[0]] += 1
            requests += int(completed)
    assert sorted(groups.values()) == [1, 1, 6, 6], groups
    cleanup = json.loads((root / platform / "dgdr-cleanup.json").read_text())
    assert cleanup["passed"]
    assert not cleanup["resources"]["dynamoworkermetadatas"]["items"]
    report[platform] = {
        "passed": True,
        "strategy": "thorough",
        "runtime_image_ids": sorted(image_ids),
        "vllm": baseline["versions"]["vllm"],
        "profiler_job": dgdr["status"]["profilingJobName"],
        "aiperf_measurement_runs": len(profiles),
        "separate_decode_warmup_runs": len(warmups),
        "completed_requests": requests,
        "candidate_runs": dict(groups),
        "auto_apply_api_sse_cancellation": "passed",
        "cancellations": 4,
        "cleanup": "passed",
        "sla_prediction_certified": False,
        "raw_archive_sha256": archive["sha256"],
    }
(root / "dgdr-summary.json").write_text(json.dumps(report, indent=2) + "\n")
print("CUDA and Ascend measured DGDR autoApply evidence validated")
