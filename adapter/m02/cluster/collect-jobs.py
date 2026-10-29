#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Archive selected campaign Job reports and provenance without printing full responses."""

import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--ssh-config", required=True)
p.add_argument("--host", required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
p.add_argument("--output", type=Path, required=True)
p.add_argument("--raw-output", type=Path, required=True)
p.add_argument("jobs", nargs="+")
args = p.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
args.raw_output.mkdir(parents=True, exist_ok=True)
license = "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\n"


def remote(cmd):
    return subprocess.run(
        [
            "ssh",
            "-F",
            args.ssh_config,
            args.host,
            shlex.join(["kubectl", "-n", args.namespace, *cmd]),
        ],
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    ).stdout


for job in args.jobs:
    meta = json.loads(remote(["get", "job", job, "-o", "json"]))
    assert meta["metadata"]["labels"].get("campaign") == args.namespace
    log = remote(["logs", "job/" + job])
    (args.raw_output / (job + ".log")).write_text(log)
    rows = [
        json.loads(line.split("=", 1)[1])
        for line in log.splitlines()
        if line.startswith("REGRESSION_RESULT=")
    ]
    if rows:
        path = args.output / (job + ".json")
        path.write_text(json.dumps(rows[-1], indent=2) + "\n")
        path.with_name(path.name + ".license").write_text(license)
    pods = json.loads(remote(["get", "pods", "-l", "job-name=" + job, "-o", "json"]))[
        "items"
    ]
    provenance = {
        "job": job,
        "status": meta["status"],
        "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
        "spec_sha256": hashlib.sha256(
            json.dumps(meta["spec"], sort_keys=True).encode()
        ).hexdigest(),
        "pods": [
            {
                "name": r["metadata"]["name"],
                "uid": r["metadata"]["uid"],
                "node": r["spec"].get("nodeName"),
                "containers": r["status"].get("containerStatuses", []),
            }
            for r in pods
        ],
    }
    path = args.output / (job + ".provenance.json")
    path.write_text(json.dumps(provenance, indent=2) + "\n")
    path.with_name(path.name + ".license").write_text(license)
    print(
        job,
        {
            "passed": rows[-1].get("passed"),
            "error": rows[-1].get("error"),
            "unique_outputs": rows[-1].get("unique_outputs"),
        }
        if rows
        else {
            "status": meta["status"],
            "tail": [line[:200] for line in log.splitlines()[-2:]],
        },
    )
