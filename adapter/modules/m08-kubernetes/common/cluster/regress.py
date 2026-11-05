#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise an installed M08 DGD; only mutate the explicitly named test DGD."""

import argparse
import json
import subprocess
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--namespace", default="dynamo-m08-1029")
parser.add_argument("--name", required=True)
parser.add_argument("--mode", choices=["aggregate", "pd"], default="aggregate")
parser.add_argument("--client", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
report = {"passed": False, "name": args.name, "mode": args.mode, "steps": []}


def kubectl(*words, data=None):
    return subprocess.check_output(
        ["kubectl", "-n", args.namespace, *words], input=data, timeout=180
    ).decode()


def snapshot(label):
    value = json.loads(kubectl("get", "dgd", args.name, "-o", "json"))
    (args.output / (label + ".dgd.json")).write_text(json.dumps(value, indent=2))
    pods = json.loads(kubectl("get", "pods", "-o", "json"))
    pods["items"] = [
        p for p in pods["items"] if p["metadata"]["name"].startswith(args.name + "-")
    ]
    (args.output / (label + ".pods.json")).write_text(json.dumps(pods, indent=2))
    return value, pods["items"]


def wait_ready(expected, label):
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        dgd, pods = snapshot("latest")
        active = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
        ready = [
            p
            for p in active
            if any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in p.get("status", {}).get("conditions", [])
            )
        ]
        status = dgd.get("status", {})
        settled = (
            status.get("observedGeneration") == dgd["metadata"]["generation"]
            and all(
                len(c.get("componentNames", [])) == 1
                for c in status.get("components", {}).values()
            )
            and status.get("rollingUpdate", {}).get("phase", "Completed") == "Completed"
        )
        settled = settled and all(
            status.get("components", {}).get(c["name"], {}).get("replicas")
            == c["replicas"]
            and status.get("components", {}).get(c["name"], {}).get("readyReplicas")
            == c["replicas"]
            for c in dgd["spec"]["components"]
        )
        if settled and len(active) == expected and len(ready) == expected:
            snapshot(label)
            report["steps"].append({"name": label, "ready_pods": expected})
            print(label, "ready:", expected, flush=True)
            return dgd, active
        time.sleep(5)
    raise TimeoutError(label + ": DGD did not converge to the expected ready Pod count")


def api(label, workers, prefill=0):
    dgd, pods = snapshot(label + "-before")
    frontend = next(p for p in pods if "-frontend-" in p["metadata"]["name"])
    cmd = [
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
        "m08-model",
        "--namespace",
        dgd["status"]["components"]["worker" if args.mode == "aggregate" else "decode"][
            "runtimeNamespace"
        ],
        "--workers",
        str(workers),
        "--prefill-workers",
        str(prefill),
    ]
    result = subprocess.run(
        ["kubectl", "-n", args.namespace, *cmd],
        input=args.client.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1000,
    )
    log = result.stdout.decode(errors="replace")
    (args.output / (label + ".log")).write_text(log)
    if result.returncode:
        raise RuntimeError(label + ": API regression failed; inspect saved log")
    record = json.loads(
        next(
            line.split("=", 1)[1]
            for line in log.splitlines()
            if line.startswith("REGRESSION_RESULT=")
        )
    )
    assert record["passed"]
    (args.output / (label + ".json")).write_text(json.dumps(record, indent=2))
    report["steps"].append({"name": label, "passed": True})
    print(label, "passed", flush=True)


def shape(dgd, label):
    statuses = dgd["status"]["components"]
    assert statuses["frontend"]["gpusPerEngine"] == 0, statuses["frontend"]
    assert statuses["frontend"]["gpusPerReplica"] == 0, statuses["frontend"]
    for role in ["worker"] if args.mode == "aggregate" else ["prefill", "decode"]:
        status = statuses[role]
        assert status["gpusPerEngine"] == 1, status
        assert status["gpusPerReplica"] == 1, status
    report["steps"].append({"name": label, "components": statuses})


try:
    expected = 3 if args.mode == "aggregate" else 6
    workers, prefill = (2, 0) if args.mode == "aggregate" else (3, 2)
    dgd, _ = wait_ready(expected, "initial")
    shape(dgd, "resource-counts")
    api("initial-api", workers, prefill)
    if args.mode == "aggregate":
        for replicas in [3, 2]:
            kubectl(
                "patch",
                "dgd",
                args.name,
                "--type=json",
                "-p",
                json.dumps(
                    [
                        {
                            "op": "replace",
                            "path": "/spec/components/1/replicas",
                            "value": replicas,
                        }
                    ]
                ),
            )
            dgd, _ = wait_ready(replicas + 1, "scale-" + str(replicas))
            shape(dgd, "scale-" + str(replicas) + "-counts")
            api("scale-" + str(replicas) + "-api", replicas)
    _, pods = snapshot("before-recreate")
    role = "-worker-" if args.mode == "aggregate" else "-decode-"
    victim = next(p for p in pods if role in p["metadata"]["name"])
    kubectl("delete", "pod", victim["metadata"]["name"], "--wait=false")
    _, pods = wait_ready(expected, "after-recreate")
    assert victim["metadata"]["uid"] not in [p["metadata"]["uid"] for p in pods]
    report["steps"].append(
        {"name": "worker-recreated", "deleted_uid": victim["metadata"]["uid"]}
    )
    api("recovery-api", workers, prefill)
    if args.mode == "aggregate":
        kubectl(
            "patch",
            "dgd",
            args.name,
            "--type=json",
            "-p",
            json.dumps(
                [{"op": "replace", "path": "/spec/components/1/replicas", "value": 0}]
            ),
        )
        dgd, _ = wait_ready(1, "scale-zero")
        shape(dgd, "scale-zero-counts")
        assert dgd["status"]["components"]["worker"]["replicas"] == 0
    report["passed"] = True
finally:
    (args.output / "summary.json").write_text(json.dumps(report, indent=2))
