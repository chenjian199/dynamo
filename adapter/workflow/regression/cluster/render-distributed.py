#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""One Qwen3-32B instance: TP2 within each node, PP2 across two nodes."""

import argparse
import json

from render import serving

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
p.add_argument("--model-paths", type=json.loads, required=True)
args = p.parse_args()
obj = serving(
    args.platform,
    args.namespace,
    args.model_paths,
    model="Qwen3-32B",
    tp=2,
    prefix="distributed",
)
ips = (
    ["192.168.4.6", "192.168.4.14"]
    if args.platform == "cuda"
    else ["192.168.7.7", "192.168.7.5"]
)
for item in obj["items"]:
    name = item["metadata"]["name"]
    if not name.startswith("distributed-worker"):
        continue
    rank = int(name.rsplit("-", 1)[1])
    spec = item["spec"]["template"]["spec"]
    spec.update(hostNetwork=True, dnsPolicy="ClusterFirstWithHostNet")
    c = spec["containers"][0]
    c["command"].extend(
        [
            "--pipeline-parallel-size",
            "2",
            "--nnodes",
            "2",
            "--node-rank",
            str(rank),
            "--master-addr",
            ips[0],
            "--master-port",
            "29680",
            "--distributed-executor-backend",
            "mp",
            "--distributed-timeout-seconds",
            "180",
        ]
    )
    if rank:
        c["command"].append("--headless")
    env = {e["name"]: e["value"] for e in c["env"]}
    env.update(
        VLLM_HOST_IP=ips[rank],
        DYN_SYSTEM_PORT=str(18310 + rank),
        DYN_FORWARDPASS_METRIC_PORT=str(18320 + rank),
    )
    i = c["command"].index("--kv-events-config") + 1
    event = json.loads(c["command"][i])
    event["endpoint"] = f"tcp://*:{20310+rank}"
    c["command"][i] = json.dumps(event)
    if args.platform == "cuda":
        c["securityContext"] = {"capabilities": {"add": ["IPC_LOCK"]}}
        for key in ["requests", "limits"]:
            c["resources"][key]["nvidia.com/roce_h3cnic0"] = 1
        env.update(
            NCCL_DEBUG="INFO",
            NCCL_NET="IB",
            NCCL_IB_HCA="=mlx5_10",
            NCCL_SOCKET_IFNAME="=bond0",
            GLOO_SOCKET_IFNAME="bond0",
        )
    else:
        env.update(
            HCCL_SOCKET_IFNAME="bond1",
            GLOO_SOCKET_IFNAME="bond1",
            HCCL_CONNECT_TIMEOUT="180",
            HCCL_EXEC_TIMEOUT="180",
        )
    c["env"] = [{"name": k, "value": v} for k, v in env.items()]
print(json.dumps(obj, indent=2))
