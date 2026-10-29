#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""One local vLLM DP2 group with TP2 ranks, distinct from two worker Deployments."""

import argparse
import json

from render import NODES, serving

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=NODES, required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
args = p.parse_args()
path = "/root/Qwen3-32B" if args.platform == "cuda" else "/data/Qwen3-32B"
obj = serving(
    args.platform,
    args.namespace,
    {NODES[args.platform][1]: path},
    model="Qwen3-32B",
    tp=2,
    prefix="dense-dp",
)
for item in obj["items"]:
    if item["metadata"]["name"] != "dense-dp-worker-0":
        continue
    c = item["spec"]["template"]["spec"]["containers"][0]
    resource = "nvidia.com/gpu" if args.platform == "cuda" else "huawei.com/Ascend910"
    for key in ("requests", "limits"):
        c["resources"][key][resource] = 4
    c["command"] += ["--data-parallel-size", "2", "--data-parallel-size-local", "2"]
    cmd = c["command"]
    cmd[cmd.index("--max-model-len") + 1] = "16384"
    cmd[cmd.index("--max-num-seqs") + 1] = "16"
    cmd[cmd.index("--kv-cache-memory-bytes") + 1] = "4294967296"
print(json.dumps(obj, indent=2))
