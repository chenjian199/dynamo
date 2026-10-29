#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Remove this campaign's DGD workloads, retaining the installed Operator."""

import argparse
import json
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--namespace", default="dynamo-m08-1029")
p.add_argument("--names", nargs="+", required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
kube = ["kubectl", "-n", a.namespace]


def get(kind):
    return json.loads(subprocess.check_output(kube + ["get", kind, "-o", "json"]))


for dgd in get("dgd")["items"]:
    if dgd["metadata"]["name"] in a.names:
        assert dgd["metadata"].get("labels", {}).get("heyi.io/campaign") == a.namespace
        subprocess.run(
            kube + ["delete", "dgd", dgd["metadata"]["name"], "--wait=true"],
            check=True,
            timeout=180,
        )

deadline = time.monotonic() + 300
while time.monotonic() < deadline:
    resources = {k: get(k) for k in ["dgd", "pods", "dynamoworkermetadatas"]}
    # The namespace is dedicated to this test campaign. Any leftover Worker
    # metadata means service-discovery cleanup has not completed yet.
    remaining = [
        pod["metadata"]["name"]
        for pod in resources["pods"]["items"]
        if any(pod["metadata"]["name"].startswith(n + "-") for n in a.names)
    ]
    if not remaining and not resources["dynamoworkermetadatas"]["items"]:
        operator = json.loads(
            subprocess.check_output(
                kube
                + [
                    "get",
                    "deployment",
                    "heyi-m08-dynamo-operator-controller-manager",
                    "-o",
                    "json",
                ]
            )
        )
        assert operator["status"]["readyReplicas"] == 1
        result = {"passed": True, "resources": resources, "operator": operator}
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, indent=2))
        print("Test workloads and discovery metadata removed; Operator ready 1/1")
        break
    time.sleep(5)
else:
    raise TimeoutError("Test workload or discovery metadata cleanup did not converge")
