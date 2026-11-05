#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Capture namespace-scoped M08 resources and logs, never Kubernetes Secrets."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--namespace", default="dynamo-m08-1029")
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
cmd = ["kubectl", "-n", a.namespace]
for resource in [
    "dgd",
    "deployments",
    "replicasets",
    "pods",
    "services",
    "events",
    "dynamoworkermetadatas",
]:
    data = subprocess.check_output(cmd + ["get", resource, "-o", "json"], timeout=60)
    (a.output / (resource + ".json")).write_bytes(data)
pods = json.loads((a.output / "pods.json").read_text())["items"]
for pod in pods:
    for c in pod["spec"].get("initContainers", []) + pod["spec"]["containers"]:
        name = pod["metadata"]["name"]
        r = subprocess.run(
            cmd + ["logs", name, "-c", c["name"], "--tail=1000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        (a.output / (name + "." + c["name"] + ".log")).write_bytes(r.stdout)
manifest = {
    f.name: hashlib.sha256(f.read_bytes()).hexdigest()
    for f in sorted(a.output.iterdir())
    if f.is_file() and f.name != "sha256.json"
}
(a.output / "sha256.json").write_text(json.dumps(manifest, indent=2))
print("Collected", len(manifest), "files")
