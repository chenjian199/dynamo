#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Restart only the test Planner and prove it reloads the same persisted profile."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--name", required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
k = ["kubectl", "-n", "dynamo-m08-1029"]
selector = (
    f"nvidia.com/dynamo-graph-deployment-name={a.name},"
    "nvidia.com/dynamo-component=planner"
)


def pods():
    return json.loads(
        subprocess.check_output(k + ["get", "pods", "-l", selector, "-o", "json"])
    )["items"]


def receipt(pod, phase):
    log = subprocess.check_output(k + ["logs", pod["metadata"]["name"]], text=True)
    (a.output / f"{phase}.log").write_text(log)
    matches = re.findall(
        r"Offline bootstrap source=files component=(prefill|decode) samples=(\d+) fingerprint=([a-f0-9]+)",
        log,
    )
    if {role for role, _, _ in matches} != {"prefill", "decode"}:
        raise ValueError(f"{phase}: both offline bootstrap records are required")
    return {
        "pod": pod["metadata"]["name"],
        "uid": pod["metadata"]["uid"],
        "bootstrap": sorted(matches),
    }


before_pod = next(p for p in pods() if not p["metadata"].get("deletionTimestamp"))
before = receipt(before_pod, "before")
subprocess.run(k + ["delete", "pod", before["pod"], "--wait=false"], check=True)
deadline = time.monotonic() + 600
while time.monotonic() < deadline:
    ready = [
        p
        for p in pods()
        if p["metadata"]["uid"] != before["uid"]
        and not p["metadata"].get("deletionTimestamp")
        and any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in p["status"].get("conditions", [])
        )
    ]
    if ready:
        after = receipt(ready[0], "after")
        break
    time.sleep(5)
else:
    raise TimeoutError("Replacement Planner was not ready within 600 seconds")
result = {
    "before": before,
    "after": after,
    "passed": before["bootstrap"] == after["bootstrap"],
}
(a.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result), flush=True)
if not result["passed"]:
    raise SystemExit(1)
