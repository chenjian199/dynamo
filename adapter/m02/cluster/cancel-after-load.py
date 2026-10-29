#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Add cancellation checks after observed steady load, without restarting its workers."""

import argparse
import json
import shlex
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--ssh-config", required=True)
p.add_argument("--host", required=True)
p.add_argument("--manifest", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
ns = "dynamo-regression-1028"


def remote(cmd, data=None):
    return subprocess.run(
        ["ssh", "-F", a.ssh_config, a.host, shlex.join(["kubectl", "-n", ns, *cmd])],
        input=data,
        text=True,
        capture_output=True,
        timeout=45,
        check=True,
    ).stdout


start = time.monotonic()
while True:
    job = json.loads(remote(["get", "job", "pd-load-observed-30m", "-o", "json"]))
    if job["status"].get("failed"):
        raise RuntimeError("steady load failed")
    if job["status"].get("succeeded"):
        break
    if time.monotonic() - start > 3600:
        raise TimeoutError("steady load")
    time.sleep(5)
obj = json.loads(a.manifest.read_text())
obj["metadata"]["name"] = "pd-observed-cancel-32"
remote(["apply", "-f", "-"], json.dumps(obj))
start = time.monotonic()
while True:
    job = json.loads(remote(["get", "job", "pd-observed-cancel-32", "-o", "json"]))
    if job["status"].get("failed"):
        raise RuntimeError("cancellation failed")
    if job["status"].get("succeeded"):
        log = remote(["logs", "job/pd-observed-cancel-32"])
        a.output.write_text(log)
        print("cancellation completed", flush=True)
        break
    if time.monotonic() - start > 600:
        raise TimeoutError("cancellation")
    time.sleep(5)
