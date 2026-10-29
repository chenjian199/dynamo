#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dynamo KVBM + PD and tier-aware routing using the frozen runtime packages."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument(
    "--case", choices=["all", "pd", "routing-host", "routing-disk"], default="all"
)
p.add_argument("--full-logs", action="store_true")
p.add_argument("--repeat", type=int, default=1)
args = p.parse_args()
if not 1 <= args.repeat <= 5:
    p.error("--repeat must be between 1 and 5")
base = Path("/results")
base.mkdir(exist_ok=True)
records = []
cases = [
    ("pd", ["--kvbm", "--cancel"]),
    (
        "routing-host",
        ["--aggregate", "--kvbm", "--kv-routing", "--kv-routing-tier", "host"],
    ),
    (
        "routing-disk",
        ["--aggregate", "--kvbm", "--kv-routing", "--kv-routing-tier", "disk"],
    ),
]
cases = [row for row in cases if args.case in ("all", row[0])] * args.repeat
for index, (case, flags) in enumerate(cases):
    name = case if args.repeat == 1 else f"{case}-{index}"
    command = [
        sys.executable,
        "/harness/adapter/m05/run.py",
        "--platform",
        os.environ["DYN_PLATFORM"],
        "--devices",
        "0,1",
        "--model",
        "/models/Qwen3-0.6B",
        "--output",
        str(base / name),
        *flags,
    ]
    log = base / (name + ".runner.log")
    try:
        with log.open("w") as out:
            proc = subprocess.run(
                command, stdout=out, stderr=subprocess.STDOUT, timeout=1000
            )
        path = base / name / "summary.json"
        summary = json.loads(path.read_text()) if path.is_file() else {}
        row = {"case": name, "exit_code": proc.returncode, "summary": summary}
    except subprocess.TimeoutExpired as error:
        row = {"case": name, "exit_code": 124, "error": str(error)}
    records.append(row)
    print("CACHE_CASE=" + json.dumps(row), flush=True)
    if args.full_logs:
        for path in sorted((base / name).rglob("*")):
            if path.is_file() and (".log" in path.name or path.suffix == ".json"):
                print("ARTIFACT_BEGIN=" + str(path), flush=True)
                print(path.read_text(errors="replace"), flush=True)
                print("ARTIFACT_END=" + str(path), flush=True)
    if row["exit_code"]:
        print(log.read_text(errors="replace")[-15000:], flush=True)
        for path in (base / name).glob("*.log*"):
            print(path.name, path.read_text(errors="replace")[-4000:], flush=True)
        break
passed = len(records) == len(cases) and all(r["exit_code"] == 0 for r in records)
print(
    "REGRESSION_RESULT="
    + json.dumps(
        {
            "passed": passed,
            "cases": records,
            "qualification": "single Pod, two allocated devices; PD network coverage is tested separately",
        }
    ),
    flush=True,
)
sys.exit(0 if passed else 1)
