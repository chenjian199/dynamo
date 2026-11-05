#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run all six real handoff pairs from an Operator-managed 2P3D frontend."""

import argparse
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--namespace", default="dynamo-m08-1029")
p.add_argument("--name", default="m08-pd")
p.add_argument("--client", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
kube = ["kubectl", "-n", a.namespace]
dgd = json.loads(subprocess.check_output(kube + ["get", "dgd", a.name, "-o", "json"]))
components = dgd["status"]["components"]
namespace = components["decode"]["runtimeNamespace"]
assert components["prefill"]["runtimeNamespace"] == namespace
command = kube + [
    "exec",
    "-i",
    "deployment/" + a.name + "-frontend",
    "-c",
    "main",
    "--",
    "python",
    "-",
    "--namespace",
    namespace,
    "--discovery-backend",
    "kubernetes",
    "--model",
    "m08-model",
    "--prefill-workers",
    "2",
    "--decode-workers",
    "3",
    "--output",
    "/tmp/m08-pairs.json",
]
r = subprocess.run(
    command,
    input=a.client.read_bytes(),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    timeout=600,
)
log = r.stdout.decode(errors="replace")
(a.output / "pairs.log").write_text(log)
if r.returncode:
    raise RuntimeError("PD pair regression failed; see pairs.log")
result = json.loads(
    next(
        line.split("=", 1)[1]
        for line in log.splitlines()
        if line.startswith("REGRESSION_RESULT=")
    )
)
assert result["passed"] and len(result["pairs"]) == 6, result
(a.output / "pairs.json").write_text(json.dumps(result, indent=2))
print("All 2 x 3 real handoff pairs passed")
