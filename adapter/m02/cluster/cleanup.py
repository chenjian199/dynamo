#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Scale down this campaign's Deployments; retain completed Jobs and refuse active Jobs."""
import argparse
import json
import shlex
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--ssh-config", required=True)
p.add_argument("--host", required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
p.add_argument("--execute", action="store_true")
a = p.parse_args()


def remote(command):
    return subprocess.check_output(
        [
            "ssh",
            "-F",
            a.ssh_config,
            a.host,
            shlex.join(["kubectl", "-n", a.namespace, *command]),
        ],
        text=True,
        timeout=60,
    )


selector = "campaign=" + a.namespace
jobs = json.loads(remote(["get", "jobs", "-l", selector, "-o", "json"]))["items"]
active = [j["metadata"]["name"] for j in jobs if j.get("status", {}).get("active")]
if active:
    raise SystemExit(
        "Active campaign Jobs must finish before cleanup: " + ", ".join(active)
    )
deployments = json.loads(remote(["get", "deployments", "-l", selector, "-o", "json"]))[
    "items"
]
names = [d["metadata"]["name"] for d in deployments if d["spec"].get("replicas", 1) > 0]
print(
    json.dumps(
        {
            "host": a.host,
            "namespace": a.namespace,
            "execute": a.execute,
            "scale_to_zero": names,
        }
    )
)
if a.execute and names:
    print(remote(["scale", "deployment", *names, "--replicas=0"]))
