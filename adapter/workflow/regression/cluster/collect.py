#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect this campaign's Pod logs and compact results through existing SSH config."""

import argparse
import datetime
import json
import shlex
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-config", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--namespace", default="dynamo-regression-1028")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    def remote(command):
        return subprocess.run(
            ["ssh", "-F", args.ssh_config, args.host, shlex.join(command)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout

    prefix = ["kubectl", "-n", args.namespace]
    pods = json.loads(
        remote(
            [*prefix, "get", "pods", "-l", f"campaign={args.namespace}", "-o", "json"]
        )
    )["items"]
    records = []
    for pod in pods:
        name = pod["metadata"]["name"]
        record = {
            "pod": name,
            "node": pod["spec"].get("nodeName"),
            "phase": pod["status"]["phase"],
            "containers": [
                {
                    "name": c["name"],
                    "image_id": c.get("imageID"),
                    "restart_count": c["restartCount"],
                    "state": c["state"],
                }
                for c in pod["status"].get("containerStatuses", [])
            ],
        }
        if pod["status"]["phase"] != "Pending":
            try:
                log = remote([*prefix, "logs", name, "--timestamps=false"])
                (args.output / f"{name}.log").write_text(log)
                record["results"] = [
                    json.loads(line.split("=", 1)[1])
                    for line in log.splitlines()
                    if line.startswith("REGRESSION_RESULT=")
                ]
                record["pytest_summary"] = [
                    line
                    for line in log.splitlines()
                    if " passed" in line and line.startswith("=")
                ]
            except (subprocess.SubprocessError, json.JSONDecodeError) as error:
                record["collection_error"] = str(error)
        records.append(record)
    report = {
        "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": args.host,
        "namespace": args.namespace,
        "pods": records,
    }
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    for r in records:
        print(
            r["pod"],
            r["phase"],
            [v.get("passed") for v in r.get("results", [])],
            r.get("pytest_summary", []),
        )


if __name__ == "__main__":
    main()
