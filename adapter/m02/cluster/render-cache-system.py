#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Mount only the existing test harness, never production source, into the runtime."""

import argparse
import json
from pathlib import Path

from render import NODES, pod_spec

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=NODES, required=True)
p.add_argument("--model-path", required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
p.add_argument("--name", default="cache-system")
p.add_argument(
    "--case", choices=["all", "pd", "routing-host", "routing-disk"], default="all"
)
p.add_argument("--full-logs", action="store_true")
p.add_argument("--repeat", type=int, default=1)
args = p.parse_args()
root = Path(__file__).resolve().parents[3]
files = [
    "adapter/m06/run.py",
    "adapter/m06/sources.txt",
    "adapter/m05/run.py",
    "tests/__init__.py",
    "tests/utils/managed_process.py",
    "tests/utils/port_utils.py",
    "tests/utils/constants.py",
    "tests/utils/test_output.py",
    "adapter/m02/cluster/cache-system.py",
    "adapter/m06/routing.py",
    "adapter/m05/patch_vendor.py",
    "adapter/m05/patches/manifest.json",
    "adapter/m05/patches/ascend-tp-manifest.json",
]


def meta(name):
    return {
        "name": name,
        "namespace": args.namespace,
        "labels": {"campaign": args.namespace},
    }


cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": meta(args.name + "-scripts"),
    "data": {str(i): (root / path).read_text() for i, path in enumerate(files)},
}
spec = pod_spec(
    args.platform,
    NODES[args.platform][0],
    [
        "python",
        "/harness/adapter/m02/cluster/cache-system.py",
        "--case",
        args.case,
        "--repeat",
        str(args.repeat),
    ]
    + (["--full-logs"] if args.full_logs else []),
    devices=2,
)
c = spec["containers"][0]
c["env"].extend(
    [
        {"name": "SPT_NOENV", "value": "1"},
        {"name": "GOMAXPROCS", "value": "4"},
        {
            "name": "UCX_TLS",
            "value": "tcp,sm,self,cuda_copy,cuda_ipc"
            if args.platform == "cuda"
            else "tcp,sm,self",
        },
    ]
)
c["resources"]["limits"].update(cpu="16", memory="64Gi")
c["resources"]["requests"].update(cpu="4", memory="8Gi")
spec["volumes"].extend(
    [
        {
            "name": "harness",
            "configMap": {
                "name": cm["metadata"]["name"],
                "items": [
                    {"key": str(i), "path": path} for i, path in enumerate(files)
                ],
            },
        },
        {"name": "model", "hostPath": {"path": args.model_path, "type": "Directory"}},
        {"name": "results", "emptyDir": {"sizeLimit": "16Gi"}},
    ]
)
c["volumeMounts"].extend(
    [
        {"name": "harness", "mountPath": "/harness", "readOnly": True},
        {"name": "model", "mountPath": "/models/Qwen3-0.6B", "readOnly": True},
        {"name": "results", "mountPath": "/results"},
    ]
)
job = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": meta(args.name),
    "spec": {
        "backoffLimit": 0,
        "activeDeadlineSeconds": 3600,
        "template": {
            "metadata": {"labels": {"campaign": args.namespace}},
            "spec": spec,
        },
    },
}
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [cm, job]}, indent=2))
