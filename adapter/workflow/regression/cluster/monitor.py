#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded snapshots of selected regression Deployments through kubectl exec."""

import argparse
import json
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ssh-config", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--namespace", default="dynamo-regression-1028")
    p.add_argument("--deployments", nargs="+", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--samples", type=int, default=34)
    p.add_argument("--wait-job", help="Wait for its WARMUP_COMPLETE log marker")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).with_name("resources.py").read_text()

    def sample(name):
        cmd = [
            "kubectl",
            "-n",
            args.namespace,
            "exec",
            "-i",
            f"deployment/{name}",
            "--",
            "/opt/dynamo/venv/bin/python",
            "-",
        ]
        try:
            proc = subprocess.run(
                ["ssh", "-F", args.ssh_config, args.host, shlex.join(cmd)],
                input=script,
                text=True,
                capture_output=True,
                timeout=45,
                check=True,
            )
            return {"deployment": name, "snapshot": json.loads(proc.stdout)}
        except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
            return {"deployment": name, "error": str(exc)}

    if args.wait_job:
        deadline = time.monotonic() + 1800
        while True:
            cmd = shlex.join(
                ["kubectl", "-n", args.namespace, "logs", "job/" + args.wait_job]
            )
            proc = subprocess.run(
                ["ssh", "-F", args.ssh_config, args.host, cmd],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if "WARMUP_COMPLETE" in proc.stdout:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("No warmup marker from " + args.wait_job)
            time.sleep(5)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i in range(args.samples):
            started = time.monotonic()
            rows = list(pool.map(sample, args.deployments))
            (args.output / f"{i:03d}.json").write_text(
                json.dumps(rows, indent=2) + "\n"
            )
            print(
                json.dumps(
                    {"sample": i, "errors": sum("error" in row for row in rows)}
                ),
                flush=True,
            )
            if i + 1 < args.samples:
                time.sleep(max(0, 60 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
