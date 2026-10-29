#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prepare bounded TP4 DeepSeek/CUDA or Qwen2.5-VL/Ascend aggregate checks."""

import argparse
import json

from render import NODES, serving

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
args = p.parse_args()
cuda = args.platform == "cuda"
prefix = "deepseek" if cuda else "vision"
model = "DeepSeek-V4-Flash-0731" if cuda else "Qwen2.5-VL-72B-Instruct"
path = (
    "/ssd/wtaobj/models/DeepSeek-V4-Flash-0731"
    if cuda
    else "/data/weights/Qwen2.5-VL-72B-Instruct"
)
obj = serving(
    args.platform,
    args.namespace,
    {NODES[args.platform][0]: path},
    model=model,
    tp=4,
    prefix=prefix,
)
for item in obj["items"]:
    name = item["metadata"]["name"]
    if item["kind"] != "Deployment" or not name.startswith(prefix):
        continue
    c = item["spec"]["template"]["spec"]["containers"][0]
    if name.endswith("frontend"):
        c["command"][c["command"].index("--router-mode") + 1] = "round-robin"
        continue
    cmd = c["command"]
    cmd[cmd.index("--enable-prefix-caching")] = "--no-enable-prefix-caching"
    i = cmd.index("--kv-events-config")
    del cmd[i : i + 2]
    c["resources"]["requests"].update(cpu="8", memory="64Gi")
    c["resources"]["limits"].update(cpu="32", memory="256Gi")
    if cuda:
        cmd[cmd.index("--block-size") + 1] = "256"
        cmd[cmd.index("--kv-cache-memory-bytes") + 1] = "4294967296"
        cmd.extend(
            [
                "--kv-cache-dtype",
                "fp8",
                "--tokenizer-mode",
                "deepseek_v4",
                "--reasoning-parser",
                "deepseek_v4",
                "--no-enable-flashinfer-autotune",
            ]
        )
    else:
        cmd.extend(["--enable-multimodal"])
print(json.dumps(obj, indent=2))
