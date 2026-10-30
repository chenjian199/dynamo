#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Summarize receipts without treating missing or failed stages as passing."""
import json
from pathlib import Path

from check_online_planning import audit

root = Path(__file__).parent / "results/offline"
cases = [
    ("cuda-small", "online-burst", "restart-final"),
    ("ascend-small", "online-bounded", "restart-bounded"),
    ("cuda-qwen32", "online-bounded", "restart-bounded"),
    ("ascend-qwen32", "online-final", "restart-final"),
    ("cuda-deepseek", "online-final", "restart-final"),
    ("ascend-deepseek", "online-final", "restart-final"),
]
results = []
for name, online, restart in cases:
    base = root / name

    def read(path):
        f = base / path
        return json.loads(f.read_text()) if f.exists() else None

    execution = read("profile-execution-audit.json")
    offline = read("offline-verification-final.json")
    online_result = read(online + "/result.json")
    reboot = read(restart + "/result.json")
    smoke = read(restart + "-smoke.json")
    # The first completed small CUDA run predates restart_smoke.py.
    if name == "cuda-small" and smoke is None:
        f = base / "restart-smoke.log"
        if f.exists():
            status, payload = f.read_text().split(" ", 1)
            response = json.loads(payload)
            smoke = {
                "legacy_log": "restart-smoke.log",
                "passed": status == "200"
                and bool(response.get("choices"))
                and response.get("usage", {}).get("completion_tokens", 0) > 0,
            }
    path = base / online / "load.log"
    loads = (
        [
            json.loads(s[7:])
            for s in path.read_text().splitlines()
            if s.startswith("RESULT=")
        ]
        if path.exists()
        else []
    )
    load = loads[-1] if loads else None
    planning = None
    planner_log = base / online / "planner.selected.log"
    if offline and planner_log.exists() and path.exists():
        planning = audit(planner_log, path, offline["fingerprint"])
        (base / online / "planning-audit.json").write_text(
            json.dumps(planning, indent=2) + "\n"
        )
    checks = [execution, offline, online_result, planning, reboot, smoke]
    results.append(
        {
            "case": name,
            "status": (
                (
                    "passed_with_limitations"
                    if any(v["model_not_ready"] for v in planning["stages"].values())
                    else "passed"
                )
                if all(x and x.get("passed") for x in checks)
                else (
                    "failed"
                    if any(x is not None and x.get("passed") is False for x in checks)
                    else "incomplete"
                )
            ),
            "offline_samples": (
                {m["role"]: m["samples"] for m in offline["models"]}
                if offline
                else None
            ),
            "fingerprint": offline["fingerprint"] if offline else None,
            "online": online_result,
            "online_planning": planning,
            "planner_restart": reboot["passed"] if reboot else None,
            "post_restart_api": smoke,
            "latency": load["stages"] if load else None,
            "api_errors": len(load["errors"]) if load else None,
        }
    )
(root / "summary.json").write_text(
    json.dumps(
        {
            "scope": "bounded offline bootstrap functionality; not production SLA certification",
            "cases": results,
            "passed": all(r["status"].startswith("passed") for r in results),
        },
        indent=2,
    )
    + "\n"
)
for row in results:
    print(row["case"], row["status"])
