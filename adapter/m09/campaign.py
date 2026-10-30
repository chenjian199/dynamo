#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Observe real Planner actuation during bounded load; never set replica counts."""
import argparse
import atexit
import json
import subprocess
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--name", default="m09-small")
parser.add_argument("--tools-pod", default="m09-tools")
parser.add_argument("--disagg", action="store_true")
parser.add_argument("--namespace", default="dynamo-m08-1029")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--load-script", type=Path, required=True)
parser.add_argument("--duration", type=int, default=300)
parser.add_argument("--recovery", type=int, default=120)
parser.add_argument("--concurrency", type=int, default=16)
parser.add_argument("--isl", type=int, default=128)
parser.add_argument("--osl", type=int, default=256)
parser.add_argument("--token-id-metrics", action="store_true")
parser.add_argument("--settle-timeout", type=int, default=600)
parser.add_argument(
    "--minimum-peak-ready",
    type=int,
    default=2,
    help="Use 1 for SLA checks where a HOLD decision can be correct",
)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
(args.output / "contract.json").write_text(
    json.dumps(
        {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        indent=2,
    )
    + "\n"
)
k = ["kubectl", "-n", args.namespace]
followers = {}


@atexit.register
def stop_log_followers():
    for process, stream in followers.values():
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        stream.close()


def get(resource, *rest):
    return json.loads(
        subprocess.check_output(k + ["get", resource, *rest, "-o", "json"], timeout=30)
    )


def snapshot():
    dgd = get("dgd", args.name)
    adapters = get("dynamographdeploymentscalingadapters")
    desired_by_component = {
        item["spec"]["dgdRef"]["componentName"]: item["spec"]["replicas"]
        for item in adapters["items"]
        if item["spec"]["dgdRef"]["name"] == args.name
    }
    pods = get("pods", "-l", f"nvidia.com/dynamo-graph-deployment-name={args.name}")
    for pod in pods["items"]:
        name = pod["metadata"]["name"]
        if name not in followers and pod["status"].get("phase") == "Running":
            stream = (args.output / (name + ".log")).open("wb")
            followers[name] = (
                subprocess.Popen(
                    k + ["logs", "--follow", name, "--timestamps"],
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                ),
                stream,
            )
    worker = next(
        c for c in dgd["spec"]["components"] if c["name"] == "VllmDecodeWorker"
    )
    status = dgd.get("status", {}).get("components", {}).get("VllmDecodeWorker", {})
    record = {
        "time": time.time(),
        "desired": desired_by_component.get(worker["name"], worker["replicas"]),
        "desired_source": "DGDSA" if worker["name"] in desired_by_component else "DGD",
        "ready": status.get("readyReplicas", 0),
        "generation": dgd["metadata"]["generation"],
        "observed_generation": dgd.get("status", {}).get("observedGeneration"),
        "gpus_per_replica": status.get("gpusPerReplica"),
        "worker_pods": sum(
            p["metadata"].get("labels", {}).get("nvidia.com/dynamo-component")
            == "VllmDecodeWorker"
            for p in pods["items"]
        ),
        "terminating_pods": sum(
            bool(p["metadata"].get("deletionTimestamp")) for p in pods["items"]
        ),
    }
    if args.disagg:
        prefill = next(
            c for c in dgd["spec"]["components"] if c["name"] == "VllmPrefillWorker"
        )
        p_status = (
            dgd.get("status", {}).get("components", {}).get("VllmPrefillWorker", {})
        )
        record["prefill_desired"] = desired_by_component.get(
            prefill["name"], prefill["replicas"]
        )
        record["prefill_ready"] = p_status.get("readyReplicas", 0)
    with (args.output / "scaling.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    (args.output / "latest-dgd.json").write_text(json.dumps(dgd, indent=2))
    (args.output / "latest-pods.json").write_text(json.dumps(pods, indent=2))
    (args.output / "latest-scaling-adapters.json").write_text(
        json.dumps(adapters, indent=2)
    )
    return record


deadline = time.monotonic() + 1200
while time.monotonic() < deadline:
    obj = get("dgd", args.name)
    statuses = obj.get("status", {}).get("components", {})
    observed = obj.get("status", {}).get("observedGeneration")
    if (
        observed == obj["metadata"]["generation"]
        and statuses
        and all(
            statuses.get(c["name"], {}).get("readyReplicas") == c["replicas"]
            for c in obj["spec"]["components"]
        )
    ):
        break
    time.sleep(5)
else:
    raise TimeoutError("DGD initial readiness timeout")
initial = snapshot()
with (
    args.load_script.open("rb") as source,
    (args.output / "load.log").open("wb") as log,
):
    process = subprocess.Popen(
        k
        + [
            "exec",
            "-i",
            args.tools_pod,
            "--",
            "/opt/dynamo/venv/bin/python",
            "-",
            "--url",
            f"http://{args.name}-frontend:8000",
            "--duration",
            str(args.duration),
            "--recovery",
            str(args.recovery),
            "--concurrency",
            str(args.concurrency),
            "--isl",
            str(args.isl),
            "--osl",
            str(args.osl),
        ]
        + (["--token-id-metrics"] if args.token_id_metrics else []),
        stdin=source,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    records = [initial]
    while process.poll() is None:
        records.append(snapshot())
        time.sleep(5)
    code = process.wait()
# Give scale-down enough quiet time after the last request finishes.
deadline = time.monotonic() + args.settle_timeout
while time.monotonic() < deadline:
    record = snapshot()
    records.append(record)
    if (
        record["desired"] == record["ready"] == record["worker_pods"] == 1
        and not record["terminating_pods"]
        and (
            not args.disagg or record["prefill_desired"] == record["prefill_ready"] == 1
        )
    ):
        break
    time.sleep(5)
for role in ("planner", "VllmDecodeWorker"):
    result = subprocess.run(
        k
        + [
            "logs",
            "-l",
            f"nvidia.com/dynamo-graph-deployment-name={args.name},nvidia.com/dynamo-component={role}",
            "--tail=-1",
            "--max-log-requests=8",
        ],
        capture_output=True,
        timeout=60,
    )
    (args.output / (role + ".log")).write_bytes(result.stdout + result.stderr)
result = {
    "api_exit_code": code,
    "initial": initial,
    "peak_desired": max(r["desired"] for r in records),
    "peak_ready": max(r["ready"] for r in records),
    "final": records[-1],
    "scaling_source": "Planner; campaign never patches replicas",
}
result["passed"] = (
    code == 0
    and result["peak_ready"] >= args.minimum_peak_ready
    and records[-1]["desired"]
    == records[-1]["ready"]
    == records[-1]["worker_pods"]
    == 1
    and records[-1]["terminating_pods"] == 0
    and (
        not args.disagg
        or records[-1]["prefill_desired"] == records[-1]["prefill_ready"] == 1
    )
)
(args.output / "result.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
if not result["passed"]:
    raise SystemExit(1)
