#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Require loaded file profiles and real P/D planning during measured requests."""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


def audit(planner: Path, load: Path, fingerprint: str) -> dict:
    requests = [
        json.loads(line[len("REQUEST=") :])
        for line in load.read_text().splitlines()
        if line.startswith("REQUEST=")
    ]
    windows = {}
    for request in requests:
        start = request["completed_at"] - request["e2e_ms"] / 1000
        end = request["completed_at"]
        stage = request["stage"]
        old = windows.get(stage, [start, end])
        windows[stage] = [min(start, old[0]), max(end, old[1])]
    counts = {stage: {"valid_decisions": 0, "model_not_ready": 0} for stage in windows}
    loaded = set()
    capacities = {}
    for line in planner.read_text().splitlines():
        boot = re.search(
            r"Offline bootstrap source=files component=(prefill|decode) "
            r"samples=(\d+) fingerprint=([a-f0-9]+)",
            line,
        )
        if boot and boot[3] == fingerprint and int(boot[2]) > 0:
            loaded.add(boot[1])
        timestamp = re.search(r"\d{4}-\d\d-\d\dT[\d:.]+Z", line)
        if not timestamp:
            continue
        now = datetime.fromisoformat(timestamp[0].replace("Z", "+00:00")).timestamp()
        capacity = re.search(r"(Prefill|Decode): ([0-9.]+) rps / ([0-9.]+) =", line)
        if capacity:
            capacities[capacity[1].lower()] = (
                now,
                float(capacity[2]),
                float(capacity[3]),
            )
        if "[summary]" not in line:
            continue
        for stage, (start, end) in windows.items():
            if not start <= now <= end:
                continue
            if "throughput_reason=model_not_ready" in line:
                counts[stage]["model_not_ready"] += 1
            if (
                "throughput_reason=scale" in line
                and loaded == {"prefill", "decode"}
                and set(capacities) == loaded
                and all(
                    0 <= now - at <= 2 and demand > 0 and rps > 0
                    for at, demand, rps in capacities.values()
                )
            ):
                counts[stage]["valid_decisions"] += 1
        capacities.clear()
    return {
        "scope": "positive P/D capacity decisions under actual load; not SLA certification",
        "fingerprint": fingerprint,
        "loaded_roles": sorted(loaded),
        "stage_windows": windows,
        "stages": counts,
        "evidence": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (planner, load)
        },
        "passed": counts.get("load", {}).get("valid_decisions", 0) > 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planner", type=Path, required=True)
    parser.add_argument("--load", type=Path, required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.planner, args.load, args.fingerprint)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)
