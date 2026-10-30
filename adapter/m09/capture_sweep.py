#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Capture a live interpolation engine before Profiler deletes its deployment."""
import argparse
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--deployment", required=True)
p.add_argument("--workdir", type=Path, required=True)
p.add_argument("--role", choices=["prefill", "decode"], default="decode")
a = p.parse_args()
k = ["kubectl", "-n", "dynamo-m08-1029"]
prefix = "prefill-" if a.role == "prefill" else ""
metadata = json.loads(
    subprocess.check_output(k + ["get", "dynamoworkermetadata", "-o", "json"])
)
metadata["items"] = [
    i for i in metadata["items"] if i["metadata"]["name"].startswith(a.deployment + "-")
]
if not metadata["items"]:
    raise SystemExit("No worker metadata for this sweep")
(a.workdir / (prefix + "sweep-mdc.json")).write_text(
    json.dumps(metadata, indent=2) + "\n"
)
for resource in ["pods", "dgd"]:
    selector = (
        ["-l", "nvidia.com/dynamo-graph-deployment-name=" + a.deployment]
        if resource == "pods"
        else [a.deployment]
    )
    result = subprocess.check_output(k + ["get", resource, *selector, "-o", "json"])
    (a.workdir / (prefix + "sweep-" + resource + ".json")).write_bytes(result)
subprocess.run(
    [
        "python3",
        str(Path(__file__).with_name("provenance.py")),
        "--name",
        a.deployment,
        "--output",
        str(a.workdir / (prefix + "sweep-provenance.json")),
    ],
    check=True,
)
