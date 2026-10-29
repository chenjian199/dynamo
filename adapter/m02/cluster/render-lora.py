#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render a dedicated nonzero LoRA fixture worker and lifecycle client."""

import argparse
import json
from pathlib import Path

from render import NODES, pod_spec, serving

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=NODES, required=True)
p.add_argument("--model-path", required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
args = p.parse_args()
root = Path(__file__).resolve().parents[3]
obj = serving(
    args.platform,
    args.namespace,
    {NODES[args.platform][0]: args.model_path},
    prefix="lora",
)
meta = {"namespace": args.namespace, "labels": {"campaign": args.namespace}}
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {**meta, "name": "regression-lora-fixture"},
    "data": {
        "create-lora.py": (root / "adapter/m03/create-lora.py").read_text(),
        "advanced-smoke.py": (root / "adapter/m03/advanced-smoke.py")
        .read_text()
        .replace("m03-model", "lora-model"),
        "lora-client.py": Path(__file__).with_name("lora-client.py").read_text(),
    },
}
obj["items"].append(cm)
for item in obj["items"]:
    if item["kind"] == "Deployment" and item["metadata"]["name"].startswith("lora-"):
        item["spec"]["template"]["spec"]["containers"][0]["env"].append(
            {"name": "DYN_LORA_ENABLED", "value": "true"}
        )
    if item["metadata"]["name"] != "lora-worker-0":
        continue
    spec = item["spec"]["template"]["spec"]
    c = spec["containers"][0]
    c["command"].extend(["--enable-lora", "--max-loras", "2", "--max-lora-rank", "8"])
    adapter_path = f"/var/cache/dynamo-{args.platform}/models"
    spec["volumes"].extend(
        [
            {"name": "fixtures", "configMap": {"name": "regression-lora-fixture"}},
            {"name": "adapters", "emptyDir": {"sizeLimit": "1Gi"}},
        ]
    )
    c["volumeMounts"].append(
        {"name": "adapters", "mountPath": adapter_path, "readOnly": True}
    )
    spec["initContainers"] = [
        {
            "name": "adapter",
            "image": c["image"],
            "imagePullPolicy": "Never",
            "command": [
                "/opt/dynamo/bin/entrypoint.sh",
                "python",
                "/fixtures/create-lora.py",
                "--model",
                "/models/Qwen3-0.6B",
                "--output",
                adapter_path + "/m03-lora",
            ],
            "env": [{"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"}],
            "resources": {
                "requests": {"cpu": "1", "memory": "1Gi"},
                "limits": {"cpu": "4", "memory": "8Gi"},
            },
            "volumeMounts": [
                {"name": "fixtures", "mountPath": "/fixtures", "readOnly": True},
                {"name": "adapters", "mountPath": adapter_path},
                {"name": "model", "mountPath": "/models/Qwen3-0.6B", "readOnly": True},
            ],
        }
    ]
obj["items"].append(
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {**meta, "name": "lora-worker-api"},
        "spec": {
            "selector": {"app": "lora-worker-0"},
            "ports": [{"port": 8081, "targetPort": 8081}],
        },
    }
)
spec = pod_spec(
    args.platform,
    NODES[args.platform][0],
    ["python", "/fixtures/lora-client.py"],
    devices=0,
)
spec["containers"][0]["env"].append({"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"})
spec["volumes"].append(
    {"name": "fixtures", "configMap": {"name": "regression-lora-fixture"}}
)
spec["containers"][0]["volumeMounts"].append(
    {"name": "fixtures", "mountPath": "/fixtures", "readOnly": True}
)
obj["items"].append(
    {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {**meta, "name": "lora-client"},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 1800,
            "template": {
                "metadata": {"labels": {"campaign": args.namespace}},
                "spec": spec,
            },
        },
    }
)
print(json.dumps(obj, indent=2))
