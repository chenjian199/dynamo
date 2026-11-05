#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify two Operator workers on distinct nodes with node-local model paths."""

import argparse
import json
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--client", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
cmd = ["kubectl", "-n", "dynamo-m08-1029"]
end = time.monotonic() + 900
while time.monotonic() < end:
    dgd = json.loads(
        subprocess.check_output(cmd + ["get", "dgd", "m08-cross", "-o", "json"])
    )
    if any(
        c["type"] == "Ready" and c["status"] == "True"
        for c in dgd.get("status", {}).get("conditions", [])
    ):
        break
    time.sleep(5)
else:
    raise TimeoutError("Cross-node DGD not ready")
components = dgd["status"]["components"]
assert (
    components["worker"]["runtimeNamespace"]
    == components["remote-worker"]["runtimeNamespace"]
)
for role in ["worker", "remote-worker"]:
    assert components[role]["gpusPerReplica"] == 1
pods = json.loads(subprocess.check_output(cmd + ["get", "pods", "-o", "json"]))["items"]
workers = [
    p
    for p in pods
    if p["metadata"]["name"].startswith("m08-cross-")
    and "-frontend-" not in p["metadata"]["name"]
]
assert len(workers) == 2 and len({p["spec"]["nodeName"] for p in workers}) == 2
(a.output / "dgd.json").write_text(json.dumps(dgd, indent=2))
(a.output / "pods.json").write_text(json.dumps(workers, indent=2))
r = subprocess.run(
    cmd
    + [
        "exec",
        "-i",
        "deployment/m08-cross-frontend",
        "-c",
        "main",
        "--",
        "python",
        "-",
        "--url",
        "http://127.0.0.1:8000",
        "--model",
        "m08-model",
        "--workers",
        "2",
        "--namespace",
        components["worker"]["runtimeNamespace"],
    ],
    input=a.client.read_bytes(),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    timeout=1000,
)
log = r.stdout.decode(errors="replace")
(a.output / "api.log").write_text(log)
assert r.returncode == 0, "API checks failed; see api.log"
result = json.loads(
    next(
        line.split("=", 1)[1]
        for line in log.splitlines()
        if line.startswith("REGRESSION_RESULT=")
    )
)
assert result["passed"]
result["nodes"] = sorted({p["spec"]["nodeName"] for p in workers})
(a.output / "summary.json").write_text(json.dumps(result, indent=2))
print("Cross-node aggregate/KV/API checks passed:", result["nodes"])
