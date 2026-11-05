#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Capture selected campaign workers before scale-down, including listener health and image IDs."""
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
p.add_argument("--prefix", nargs="+", required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--logs", type=Path, required=True)
a = p.parse_args()
a.logs.mkdir(parents=True, exist_ok=True)


def remote(args):
    return subprocess.check_output(
        [
            "ssh",
            "-F",
            a.ssh_config,
            a.host,
            shlex.join(["kubectl", "-n", a.namespace, *args]),
        ],
        timeout=60,
        text=True,
    )


pods = json.loads(
    remote(["get", "pods", "-l", "campaign=" + a.namespace, "-o", "json"])
)["items"]
rows = []
for pod in pods:
    app = pod["metadata"].get("labels", {}).get("app", "")
    if (
        not any(app.startswith(prefix + "-") for prefix in a.prefix)
        or pod["status"]["phase"] != "Running"
    ):
        continue
    name = pod["metadata"]["name"]
    log = remote(["logs", name])
    (a.logs / (name + ".log")).write_text(log)
    rows.append(
        {
            "pod": name,
            "uid": pod["metadata"]["uid"],
            "node": pod["spec"]["nodeName"],
            "app": app,
            "containers": pod["status"].get("containerStatuses", []),
            "template_containers": pod["spec"]["containers"],
            "volumes": pod["spec"].get("volumes", []),
            "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
            "listener_exception": "Exception in thread nixl_handshake_listener" in log,
            "address_in_use": "Address already in use" in log,
        }
    )
a.output.write_text(json.dumps({"host": a.host, "workers": rows}, indent=2) + "\n")
a.output.with_name(a.output.name + ".license").write_text(
    "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\n"
)
print(
    json.dumps(
        [
            {
                "pod": r["pod"],
                "listener_exception": r["listener_exception"],
                "address_in_use": r["address_in_use"],
            }
            for r in rows
        ]
    )
)
