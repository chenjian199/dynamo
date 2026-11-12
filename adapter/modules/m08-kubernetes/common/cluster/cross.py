#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify actual cross-node placement and API behavior of an Operator DGD."""

import argparse
import json
import subprocess
import time
from pathlib import Path

GRAPH_LABEL = "nvidia.com/dynamo-graph-deployment-name"
COMPONENT_LABEL = "nvidia.com/dynamo-component"


def placement(dgd, pods, mode):
    """Reject rolling, incomplete, or colocated workers before issuing traffic."""
    roles = ("prefill", "decode") if mode == "pd" else ("worker", "remote-worker")
    status = dgd.get("status", {})
    assert status.get("observedGeneration") == dgd["metadata"]["generation"]
    assert any(
        c["type"] == "Ready" and c["status"] == "True"
        for c in status.get("conditions", [])
    )
    specs = {c["name"]: c for c in dgd["spec"]["components"]}
    components = status["components"]
    namespace = components[roles[0]]["runtimeNamespace"]
    result = {}
    for role in roles:
        component = components[role]
        assert component["runtimeNamespace"] == namespace
        expected = specs[role]["replicas"]
        assert expected > 0 and component["readyReplicas"] == expected
        assert component["replicas"] == expected and component["gpusPerReplica"] > 0
        workers = [
            p
            for p in pods
            if p["metadata"].get("labels", {}).get(GRAPH_LABEL)
            == dgd["metadata"]["name"]
            and p["metadata"].get("labels", {}).get(COMPONENT_LABEL) == role
        ]
        assert len(workers) == expected
        assert all(
            not p["metadata"].get("deletionTimestamp")
            and p["spec"].get("nodeName")
            and any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in p.get("status", {}).get("conditions", [])
            )
            for p in workers
        )
        result[role] = workers
    # Every P->D handoff in PD mode must cross nodes; two unrelated nodes in
    # an otherwise colocated replica pool are not sufficient evidence.
    nodes = [{p["spec"]["nodeName"] for p in result[role]} for role in roles]
    assert nodes[0].isdisjoint(nodes[1]), "Worker roles are not separated across nodes"
    return namespace, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="dynamo-m08-1029")
    parser.add_argument("--name", default="m08-cross")
    parser.add_argument("--mode", choices=("aggregate", "pd"), default="aggregate")
    parser.add_argument("--model", default="m08-model")
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    kube = ["kubectl", "-n", args.namespace]
    deadline = time.monotonic() + args.timeout
    while True:
        dgd = json.loads(
            subprocess.check_output(
                kube + ["get", "dgd", args.name, "-o", "json"], timeout=60
            )
        )
        pods = json.loads(
            subprocess.check_output(kube + ["get", "pods", "-o", "json"], timeout=60)
        )["items"]
        (args.output / "dgd.json").write_text(json.dumps(dgd, indent=2) + "\n")
        selected = [
            p
            for p in pods
            if p["metadata"].get("labels", {}).get(GRAPH_LABEL) == args.name
        ]
        (args.output / "pods.json").write_text(json.dumps(selected, indent=2) + "\n")
        try:
            namespace, roles = placement(dgd, selected, args.mode)
            break
        except (AssertionError, KeyError):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Cross-node DGD placement/readiness did not converge"
                )
            time.sleep(5)
    frontend = next(
        p
        for p in selected
        if p["metadata"].get("labels", {}).get(COMPONENT_LABEL) == "frontend"
        and not p["metadata"].get("deletionTimestamp")
    )
    prefill = len(roles["prefill"]) if args.mode == "pd" else 0
    workers = (
        len(roles["decode"]) if args.mode == "pd" else sum(map(len, roles.values()))
    )
    command = kube + [
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
        args.model,
        "--workers",
        str(workers),
        "--prefill-workers",
        str(prefill),
        "--namespace",
        namespace,
    ]
    (args.output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    completed = subprocess.run(
        command,
        input=args.client.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1000,
    )
    log = completed.stdout.decode(errors="replace")
    (args.output / "api.log").write_text(log)
    completed.check_returncode()
    result = json.loads(
        next(
            line.split("=", 1)[1]
            for line in log.splitlines()
            if line.startswith("REGRESSION_RESULT=")
        )
    )
    assert result["passed"]
    result.update(
        mode=args.mode,
        placement={
            r: [
                {"pod_uid": p["metadata"]["uid"], "node": p["spec"]["nodeName"]}
                for p in entries
            ]
            for r, entries in roles.items()
        },
    )
    (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Cross-node", args.mode, "checks passed")


if __name__ == "__main__":
    main()
