#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Add test-only native lifetime observers to an existing PD regression manifest."""

import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--input", type=Path, required=True)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
args = p.parse_args()
obj = json.loads(args.input.read_text())
root = Path(__file__).resolve().parents[3]
namespace = obj["items"][0]["metadata"]["namespace"]
name = "regression-native-observer"
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {
        "name": name,
        "namespace": namespace,
        "labels": {"campaign": namespace},
    },
    "data": {
        "heyi_regression_observer.py": (root / "adapter/m05/observer.py").read_text(),
        "load.py": Path(__file__).with_name("load.py").read_text(),
    },
}
obj["items"].insert(0, cm)
for item in obj["items"]:
    if item["kind"] != "Deployment" or not item["metadata"]["name"].startswith(
        ("pd-prefill-", "pd-decode-")
    ):
        continue
    spec = item["spec"]["template"]["spec"]
    c = spec["containers"][0]
    c["command"] += [
        "--worker-extension-cls",
        "heyi_regression_observer.TransferObserver",
    ]
    c["env"] += [
        {"name": "PYTHONPATH", "value": "/observer-tools"},
        {"name": "M05_OBSERVER_DIR", "value": "/observer-state"},
        {
            "name": "M05_OBSERVER_CONNECTOR",
            "value": "nixl" if args.platform == "cuda" else "mooncake",
        },
    ]
    spec["volumes"] += [
        {"name": "observer-tools", "configMap": {"name": name}},
        {"name": "observer-state", "emptyDir": {"sizeLimit": "64Mi"}},
    ]
    c["volumeMounts"] += [
        {"name": "observer-tools", "mountPath": "/observer-tools", "readOnly": True},
        {"name": "observer-state", "mountPath": "/observer-state"},
    ]
print(json.dumps(obj, indent=2))
