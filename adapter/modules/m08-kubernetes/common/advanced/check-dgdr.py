#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Wait for a real DGDR autoApply result and verify its generated model API."""

import argparse
import json
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--namespace", default="dynamo-m08-1029")
p.add_argument("--name", default="m08-dgdr-fixed")
p.add_argument("--client", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--timeout", type=int, default=3600)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
k = ["kubectl", "-n", a.namespace]


def get(kind, name=None):
    return json.loads(
        subprocess.check_output(
            k + ["get", kind] + ([name] if name else []) + ["-o", "json"], timeout=60
        )
    )


deadline = time.monotonic() + a.timeout
previous = None
while time.monotonic() < deadline:
    dgdr = get("dgdr", a.name)
    (a.output / "dgdr.json").write_text(json.dumps(dgdr, indent=2))
    status = dgdr.get("status", {})
    state = (status.get("phase"), status.get("profilingPhase"))
    if state != previous:
        print(state, flush=True)
        previous = state
    if state[0] == "Failed":
        raise RuntimeError("DGDR failed; inspect dgdr.json and profiler logs")
    if state[0] == "Deployed":
        break
    time.sleep(10)
else:
    raise TimeoutError("DGDR did not deploy within the bounded deadline")

dgd = get("dgd", status["dgdName"])
assert dgdr["spec"]["autoApply"]
# Final deployments deliberately outlive DGDR deletion; the controller uses
# relationship labels instead of a DGDR ownerReference here.
assert dgd["metadata"]["labels"]["dgdr.nvidia.com/name"] == a.name
assert dgd["metadata"]["labels"]["dgdr.nvidia.com/namespace"] == a.namespace
assert dgd["status"]["observedGeneration"] == dgd["metadata"]["generation"]
assert any(
    c["type"] == "Ready" and c["status"] == "True" for c in dgd["status"]["conditions"]
)
pods = get("pods")
pods["items"] = [
    pod
    for pod in pods["items"]
    if pod["metadata"].get("labels", {}).get("nvidia.com/dynamo-graph-deployment-name")
    == dgd["metadata"]["name"]
]
frontend = next(pod for pod in pods["items"] if "-frontend-" in pod["metadata"]["name"])
components = dgd["status"]["components"]
decode = components["VllmDecodeWorker"]
prefill = components.get("VllmPrefillWorker", {}).get("replicas", 0)
cmd = k + [
    "exec",
    "-i",
    frontend["metadata"]["name"],
    "-c",
    "main",
    "--",
    "python",
    "-",
    "--url",
    "http://127.0.0.1:8000",
    "--model",
    dgdr["spec"]["model"],
    "--namespace",
    decode["runtimeNamespace"],
    "--workers",
    str(decode["replicas"]),
    "--prefill-workers",
    str(prefill),
    "--skip-router-kv-check",
    "--prefix-repetitions",
    "32",
    "--cancel-max-tokens",
    "512",
]
run = subprocess.run(
    cmd,
    input=a.client.read_bytes(),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    timeout=1000,
)
(a.output / "api.log").write_bytes(run.stdout)
run.check_returncode()
api = json.loads(
    next(
        line.split("=", 1)[1]
        for line in run.stdout.decode().splitlines()
        if line.startswith("REGRESSION_RESULT=")
    )
)
assert api["passed"] and api["sse_parity"] and api["post_cancel_recovery"]
for filename, data in [
    ("dgd.json", dgd),
    ("pods.json", pods),
    ("api.json", api),
    ("jobs.json", get("jobs")),
]:
    (a.output / filename).write_text(json.dumps(data, indent=2))
summary = {
    "passed": True,
    "dgdr": a.name,
    "dgd": dgd["metadata"]["name"],
    "strategy": dgdr["spec"]["searchStrategy"],
    "auto_apply": True,
    "api": "passed",
    "sse": "passed",
    "cancellations": api["cancellations"],
    "sla_prediction_certified": False,
}
(a.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary), flush=True)
