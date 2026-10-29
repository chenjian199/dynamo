#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render a run-specific CPU client Job and immutable-by-name script ConfigMap."""

import argparse
import json
from pathlib import Path

from render import NODES, pod_spec

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=NODES, required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
p.add_argument("--name", required=True)
p.add_argument(
    "--kind",
    choices=["api", "pairs", "ladder", "faults", "deepseek", "load"],
    default="api",
)
p.add_argument("--prefix", required=True)
p.add_argument("--model")
p.add_argument("--logical-namespace")
p.add_argument("--prefill", type=int, default=0)
p.add_argument("--decode", type=int, default=1)
p.add_argument("--uncached", action="store_true")
p.add_argument("--exact-long", action="store_true")
p.add_argument("--seconds", type=int, default=1800)
p.add_argument("--minimum-requests", type=int, default=1000)
p.add_argument("--observe-output-parity", action="store_true")
args = p.parse_args()
model = args.model or args.prefix + "-model"
logical = args.logical_namespace or args.prefix
file = {
    "api": "client.py",
    "pairs": "topology.py",
    "ladder": "ladder.py",
    "faults": "pd-faults.py",
    "deepseek": "deepseek-check.py",
    "load": "load.py",
}[args.kind]
cmd = ["python", "/client/" + file]
if args.kind == "api":
    cmd += [
        "--url",
        f"http://{args.prefix}-frontend:8000",
        "--model",
        model,
        "--namespace",
        logical,
        "--workers",
        str(args.decode),
        "--prefill-workers",
        str(args.prefill),
    ]
    if args.uncached:
        cmd.append("--skip-router-kv-check")
elif args.kind == "deepseek":
    cmd += ["--url", f"http://{args.prefix}-frontend:8000", "--model", model]
elif args.kind == "load":
    cmd += [
        "--url",
        f"http://{args.prefix}-frontend:8000",
        "--model",
        model,
        "--seconds",
        str(args.seconds),
        "--minimum-requests",
        str(args.minimum_requests),
    ]
    if args.observe_output_parity:
        cmd.append("--observe-output-parity")
elif args.kind == "pairs":
    cmd += [
        "--namespace",
        logical,
        "--model",
        model,
        "--prefill-workers",
        str(args.prefill),
        "--decode-workers",
        str(args.decode),
        "--output",
        "/tmp/pairs.json",
    ]
elif args.kind == "faults":
    cmd += ["--namespace", logical, "--model", model]
elif args.kind == "ladder" and args.exact_long:
    cmd += ["--exact-long"]
meta = {"namespace": args.namespace, "labels": {"campaign": args.namespace}}
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {**meta, "name": args.name + "-scripts"},
    "data": {file: Path(__file__).with_name(file).read_text()},
}
s = pod_spec(args.platform, NODES[args.platform][0], cmd, devices=0)
c = s["containers"][0]
c["env"] += [
    {"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"},
    {"name": "ETCD_ENDPOINTS", "value": "http://regression-etcd:2379"},
]
s["volumes"].append({"name": "client", "configMap": {"name": cm["metadata"]["name"]}})
c["volumeMounts"].append({"name": "client", "mountPath": "/client", "readOnly": True})
job = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {**meta, "name": args.name},
    "spec": {
        "backoffLimit": 0,
        "activeDeadlineSeconds": 2400,
        "template": {"metadata": {"labels": meta["labels"]}, "spec": s},
    },
}
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [cm, job]}, indent=2))
