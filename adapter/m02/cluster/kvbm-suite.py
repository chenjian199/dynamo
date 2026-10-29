#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the existing M06 harness against the frozen image inside one allocated Pod."""

import json
import os
import subprocess
import sys
from pathlib import Path

platform = os.environ["DYN_PLATFORM"]
base = Path("/results")
base.mkdir(exist_ok=True)
cases = [
    ("baseline", ["--baseline"]),
    (
        "host",
        [
            "--cycles",
            "4",
            "--cancel",
            "--baseline-summary",
            "/results/baseline/summary.json",
        ],
    ),
    ("disk", ["--disk", "--baseline-summary", "/results/baseline/summary.json"]),
    (
        "direct-disk",
        ["--direct-disk", "--baseline-summary", "/results/baseline/summary.json"],
    ),
    ("baseline-tp2", ["--baseline", "--tp", "2", "--devices", "0,1"]),
    (
        "tp2",
        [
            "--tp",
            "2",
            "--devices",
            "0,1",
            "--cancel",
            "--baseline-summary",
            "/results/baseline-tp2/summary.json",
        ],
    ),
]
records = []
for name, flags in cases:
    if platform == "cuda" and name in ["disk", "direct-disk"]:
        flags.append("--native-cuda")
    cmd = [
        sys.executable,
        "/harness/adapter/m06/run.py",
        "--platform",
        platform,
        "--output",
        str(base / name),
        "--model",
        "/models/Qwen3-0.6B",
        *flags,
    ]
    with (base / f"{name}.runner.log").open("w") as output:
        proc = subprocess.run(cmd, stdout=output, stderr=subprocess.STDOUT, timeout=900)
    path = base / name / "summary.json"
    report = json.loads(path.read_text()) if path.exists() else {}
    records.append({"case": name, "exit_code": proc.returncode, "summary": report})
    print("KVBM_CASE=" + json.dumps(records[-1]), flush=True)
    if proc.returncode:
        print((base / f"{name}.runner.log").read_text()[-10000:], flush=True)
        log = base / name / "engine.log.txt"
        if log.exists():
            print(log.read_text(errors="replace")[-15000:], flush=True)
        break
print(
    "REGRESSION_RESULT="
    + json.dumps(
        {
            "passed": len(records) == len(cases)
            and all(r["exit_code"] == 0 for r in records),
            "cases": records,
        }
    ),
    flush=True,
)
sys.exit(
    0 if len(records) == len(cases) and all(r["exit_code"] == 0 for r in records) else 1
)
