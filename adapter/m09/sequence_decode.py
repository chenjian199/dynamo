#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hold only this pending sweep until the preceding test has released its node.

The absent node label is temporary scheduling coordination, not an engine flag.
Persist the original selector and restore it in finally, including timeout errors.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

import yaml

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--workdir", type=Path, required=True)
p.add_argument("--profile", required=True)
p.add_argument("--release-receipt", type=Path, required=True)
p.add_argument("--release-dgd", required=True)
a = p.parse_args()
k = ["kubectl", "-n", "dynamo-m08-1029"]
config = a.workdir / a.profile / "selected_decode_interpolation/config.yaml"
deadline = time.monotonic() + 3600
while not config.exists():
    if time.monotonic() > deadline:
        raise TimeoutError("Decode config was not produced")
    time.sleep(5)
name = yaml.safe_load(config.read_text())["metadata"]["name"]
if not name.startswith("vllm-agg-"):
    raise ValueError("Not a temporary profiler deployment")
while True:
    listed = json.loads(subprocess.check_output(k + ["get", "dgd", "-o", "json"]))
    matches = [
        d
        for d in listed["items"]
        if d["metadata"]["name"] == name or d["metadata"]["name"].startswith(name + "-")
    ]
    if len(matches) > 1:
        raise ValueError("Ambiguous temporary deployment prefix")
    if matches:
        dgd = matches[0]
        name = dgd["metadata"]["name"]
        break
    if time.monotonic() > deadline:
        raise TimeoutError("Decode deployment was not created")
    time.sleep(2)
index = next(
    i
    for i, c in enumerate(dgd["spec"]["components"])
    if c["type"] in ("worker", "decode")
)
original = dgd["spec"]["components"][index]["podTemplate"]["spec"]["nodeSelector"]
path = f"/spec/components/{index}/podTemplate/spec/nodeSelector"
receipt = {"deployment": name, "original_selector": original, "held_at": time.time()}
(a.workdir / "decode-scheduling-hold.json").write_text(
    json.dumps(receipt, indent=2) + "\n"
)


def patch(selector):
    subprocess.run(
        k
        + [
            "patch",
            "dgd",
            name,
            "--type=json",
            "-p",
            json.dumps([{"op": "replace", "path": path, "value": selector}]),
        ],
        check=True,
    )


patch({**original, "heyi.io/m09-offline-release": "reserved-for-previous-campaign"})
try:
    while True:
        if a.release_receipt.exists() and json.loads(a.release_receipt.read_text()).get(
            "passed"
        ):
            pods = json.loads(
                subprocess.check_output(
                    k
                    + [
                        "get",
                        "pods",
                        "-l",
                        "nvidia.com/dynamo-graph-deployment-name=" + a.release_dgd,
                        "-o",
                        "json",
                    ]
                )
            )["items"]
            if not pods:
                break
        if time.monotonic() > deadline:
            raise TimeoutError("Previous campaign did not release its node")
        time.sleep(5)
finally:
    patch(original)
    receipt["released_at"] = time.time()
    (a.workdir / "decode-scheduling-hold.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
print(name, flush=True)
# A Pod selected before a rollout can be deleted without ever becoming Ready.
# Follow the current DGD generation instead of retaining that stale Pod set.
deadline = time.monotonic() + 1800
while time.monotonic() < deadline:
    current = json.loads(
        subprocess.check_output(k + ["get", "dgd", name, "-o", "json"])
    )
    status = current.get("status", {})
    if status.get("observedGeneration") == current["metadata"]["generation"] and any(
        c["type"] == "Ready" and c["status"] == "True"
        for c in status.get("conditions", [])
    ):
        break
    time.sleep(5)
else:
    raise TimeoutError("Decode DGD did not become ready")
subprocess.run(
    [
        "python3",
        str(Path(__file__).with_name("capture_sweep.py")),
        "--deployment",
        name,
        "--workdir",
        str(a.workdir),
    ],
    check=True,
)
