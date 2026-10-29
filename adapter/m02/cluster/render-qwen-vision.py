#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CUDA Qwen3.6 MoE vision baseline/EP using existing local model and H.264 fixture."""

import argparse
import base64
import json
from pathlib import Path

from render import NODES, pod_spec, serving

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--namespace", default="dynamo-regression-1028")
p.add_argument("--video", type=Path, required=True)
p.add_argument("--ep", action="store_true")
args = p.parse_args()
model = "Qwen3.6-35B-A3B"
obj = serving(
    "cuda",
    args.namespace,
    {NODES["cuda"][1]: "/ssd/models/Qwen/" + model},
    model=model,
    tp=2,
    prefix="vision",
)
for item in obj["items"]:
    if item["kind"] != "Deployment" or not item["metadata"]["name"].startswith(
        "vision-"
    ):
        continue
    c = item["spec"]["template"]["spec"]["containers"][0]
    cmd = c["command"]
    if item["metadata"]["name"].endswith("frontend"):
        cmd[cmd.index("--router-mode") + 1] = "round-robin"
        continue
    cmd[cmd.index("--enable-prefix-caching")] = "--no-enable-prefix-caching"
    i = cmd.index("--kv-events-config")
    del cmd[i : i + 2]
    cmd += ["--enable-multimodal", "--reasoning-parser", "qwen3"]
    if args.ep:
        cmd.append("--enable-expert-parallel")
meta = {"namespace": args.namespace, "labels": {"campaign": args.namespace}}
name = "vision-ep-client" if args.ep else "vision-client"
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {**meta, "name": name + "-scripts"},
    "data": {
        "vision-client.py": Path(__file__).with_name("vision-client.py").read_text()
    },
    "binaryData": {"red.mp4": base64.b64encode(args.video.read_bytes()).decode()},
}
s = pod_spec(
    "cuda",
    NODES["cuda"][1],
    [
        "python",
        "/media-tests/vision-client.py",
        "--model-label",
        model,
        "--h264",
        "--disable-thinking",
    ],
    devices=0,
)
s["containers"][0]["env"].append({"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"})
s["volumes"].append(
    {"name": "media-tests", "configMap": {"name": cm["metadata"]["name"]}}
)
s["containers"][0]["volumeMounts"].append(
    {"name": "media-tests", "mountPath": "/media-tests", "readOnly": True}
)
job = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {**meta, "name": name},
    "spec": {
        "backoffLimit": 0,
        "activeDeadlineSeconds": 2400,
        "template": {"metadata": {"labels": meta["labels"]}, "spec": s},
    },
}
obj["items"] += [cm, job]
print(json.dumps(obj, indent=2))
