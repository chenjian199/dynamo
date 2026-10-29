#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render fixed-image Ascend DeepSeek TP8/EP with candidate Python overlay."""

import argparse
import hashlib
import json
from pathlib import Path

from render import NODES, serving

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--namespace", default="dynamo-regression-1028")
p.add_argument("--prefix", default="ascend-deepseek")
p.add_argument("--mtp", action="store_true")
p.add_argument("--node-index", type=int, choices=(0, 1), default=1)
args = p.parse_args()
root = Path(__file__).resolve().parents[3]
files = {
    "preflight.py": "common/platform/preflight.py",
    "providers.py": "common/platform/providers.py",
    "platform.py": "vllm/platform.py",
}
data = {
    key: (root / "components/src/dynamo" / path).read_text()
    for key, path in files.items()
}
obj = serving(
    "ascend",
    args.namespace,
    {NODES["ascend"][args.node_index]: "/data/DeepSeek-V4-Flash-w8a8-mtp"},
    model="DeepSeek-V4-Flash-w8a8-mtp",
    tp=8,
    prefix=args.prefix,
)
cm_name = args.prefix + "-candidate"
obj["items"].append(
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": cm_name, "namespace": args.namespace},
        "immutable": True,
        "data": data,
    }
)
for item in obj["items"]:
    name = item["metadata"]["name"]
    if item["kind"] != "Deployment" or not name.startswith(args.prefix):
        continue
    spec = item["spec"]["template"]["spec"]
    c = spec["containers"][0]
    if name.endswith("frontend"):
        c["command"][c["command"].index("--router-mode") + 1] = "round-robin"
        continue
    cmd = c["command"]
    cmd[cmd.index("--enable-prefix-caching")] = "--no-enable-prefix-caching"
    for flag in ("--kv-events-config", "--kv-cache-memory-bytes"):
        i = cmd.index(flag)
        del cmd[i : i + 2]
    cmd.extend(
        [
            "--enable-expert-parallel",
            "--tokenizer-mode",
            "deepseek_v4",
            "--reasoning-parser",
            "deepseek_v4",
            "--quantization",
            "ascend",
            "--gpu-memory-utilization",
            "0.9",
            "--max-num-batched-tokens",
            "4096",
            "--additional-config",
            '{"enable_dsa_cp":true}',
        ]
    )
    if args.mtp:
        cmd.extend(
            [
                "--speculative-config",
                '{"num_speculative_tokens":1,"method":"mtp","enforce_eager":true}',
            ]
        )
    env = {e["name"]: e["value"] for e in c["env"]}
    env.update(
        OMP_PROC_BIND="false",
        OMP_NUM_THREADS="4",
        PYTORCH_NPU_ALLOC_CONF="expandable_segments:True",
        HCCL_BUFFSIZE="1024",
        VLLM_ASCEND_ENABLE_FLASHCOMM1="1",
        TASK_QUEUE_ENABLE="1",
        HCCL_OP_EXPANSION_MODE="AIV",
        HCCL_CONNECT_TIMEOUT="300",
        HCCL_EXEC_TIMEOUT="300",
    )
    c["env"] = [{"name": k, "value": v} for k, v in env.items()]
    c["resources"]["requests"].update(cpu="8", memory="64Gi")
    c["resources"]["limits"].update(cpu="32", memory="256Gi")
    for volume in spec["volumes"]:
        if volume["name"] == "shm":
            volume["emptyDir"]["sizeLimit"] = "16Gi"
    spec["volumes"].append({"name": "candidate", "configMap": {"name": cm_name}})
    for key, path in files.items():
        c["volumeMounts"].append(
            {
                "name": "candidate",
                "subPath": key,
                "readOnly": True,
                "mountPath": "/opt/dynamo/venv/lib/python3.12/site-packages/dynamo/"
                + path,
            }
        )
    item["spec"]["template"]["metadata"]["annotations"] = {
        "regression.heyi/candidate-sha256": hashlib.sha256(
            json.dumps(data, sort_keys=True).encode()
        ).hexdigest(),
    }
print(json.dumps(obj, indent=2))
