#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Summarize retained functional runs without treating latency misses as passes."""
import argparse
import json
import re
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--results", type=Path, required=True)
a = p.parse_args()
rows = []
for platform in ["cuda", "ascend"]:
    for name in ["small-final", "sla-metrics", "qwen32-final", "deepseek-final"]:
        root = a.results / platform / name
        if not (root / "result.json").is_file():
            continue
        result = json.loads((root / "result.json").read_text())
        if not (root / "latency.json").is_file():
            rows.append(
                dict(
                    platform=platform,
                    run=name,
                    functional_passed=False,
                    peak=result["peak_ready"],
                    stages=[],
                    reason="No completed load report; inspect load.log and result.json",
                )
            )
            continue
        latency = json.loads((root / "latency.json").read_text())
        row = dict(
            platform=platform,
            run=name,
            functional_passed=result["passed"],
            initial=result["initial"]["ready"],
            peak=result["peak_ready"],
            final=result["final"]["ready"],
            devices_per_replica=result["final"]["gpus_per_replica"],
            terminating_pods=result["final"]["terminating_pods"],
            errors=len(latency["errors"]),
            latency_basis=latency.get("latency_basis", "visible_text"),
            calibration_targets=latency["targets"],
            stages=latency["stages"],
        )
        transitions = []
        for line in (root / "scaling.jsonl").read_text().splitlines():
            sample = json.loads(line)
            if not transitions or transitions[-1] != sample["desired"]:
                transitions.append(sample["desired"])
        row["desired_replica_sequence"] = transitions
        if name == "sla-metrics":
            targets = (
                dict(ttft_p95_ms=500, tpot_p95_ms=25)
                if platform == "cuda"
                else dict(ttft_p95_ms=1000, tpot_p95_ms=100)
            )
            text = (root / "planner.selected.log").read_text()
            estimates = [float(x) for x in re.findall(r"est_itl=([0-9.]+)ms", text)]
            positive = [x for x in estimates if x > 0]
            row["sla"] = dict(
                configured_targets=targets,
                positive_itl_decisions=len(positive),
                max_estimated_itl_ms=max(positive, default=None),
                hold_decisions=text.count("[summary] HOLD"),
                measured_targets_passed=all(
                    s[k] <= v for s in latency["stages"] for k, v in targets.items()
                ),
                live_prediction_observed=bool(positive),
                limitation="Aggregated Planner TTFT estimate is zero; not proof of zero client latency or TTFT prediction accuracy",
            )
        rows.append(row)
report = dict(
    scope="Single-run functional regression; overlapping jobs are not isolated performance comparisons",
    runs=rows,
)
(a.results / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
for row in rows:
    print(
        row["platform"],
        row["run"],
        row["functional_passed"],
        row["peak"],
        row["stages"],
    )
