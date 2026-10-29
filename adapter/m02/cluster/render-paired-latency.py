#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sequential native/Dynamo latency Job; stop other campaign GPU loads before applying."""
import argparse
import json
from pathlib import Path

from render import NODES, pod_spec

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=NODES, required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
a = p.parse_args()
meta = {
    "name": "paired-latency",
    "namespace": a.namespace,
    "labels": {"campaign": a.namespace},
}
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": meta,
    "data": {
        "paired-latency.py": Path(__file__).with_name("paired-latency.py").read_text()
    },
}
s = pod_spec(
    a.platform, NODES[a.platform][0], ["python", "/client/paired-latency.py"], devices=2
)
c = s["containers"][0]
c["resources"]["limits"].update(cpu="16", memory="96Gi")
c["resources"]["requests"].update(cpu="8", memory="16Gi")
s["volumes"] += [
    {"name": "client", "configMap": {"name": "paired-latency"}},
    {
        "name": "model",
        "hostPath": {
            "path": "/ssd/models/dynamo-regression/Qwen3-32B"
            if a.platform == "cuda"
            else "/data/Qwen3-32B",
            "type": "Directory",
        },
    },
]
c["volumeMounts"] += [
    {"name": "client", "mountPath": "/client", "readOnly": True},
    {"name": "model", "mountPath": "/models/Qwen3-32B", "readOnly": True},
]
job = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": meta,
    "spec": {
        "backoffLimit": 0,
        "activeDeadlineSeconds": 2400,
        "template": {"metadata": {"labels": meta["labels"]}, "spec": s},
    },
}
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [cm, job]}, indent=2))
