#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render cluster-specific M08 DGD without changing the vendor runtime image."""

import argparse
import copy
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "m08_render", Path(__file__).resolve().parents[1] / "render.py"
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--platform", choices=["cuda", "ascend"], required=True)
parser.add_argument("--mode", choices=["aggregate", "pd"], default="aggregate")
parser.add_argument("--namespace", default="dynamo-m08-1029")
parser.add_argument("--name", default="m08-aggregate")
parser.add_argument("--node", required=True)
parser.add_argument("--model-host-path", required=True)
parser.add_argument(
    "--second-node", help="Place a second aggregate worker or all decode workers here"
)
parser.add_argument("--second-model-host-path", help="Model path on the second node")
args = parser.parse_args()
if bool(args.second_node) != bool(args.second_model_host_path):
    parser.error("--second-node and --second-model-host-path must be provided together")
if args.second_node == args.node:
    parser.error("Cross-node mode requires two distinct nodes")
obj = base.render(
    argparse.Namespace(
        platform=args.platform,
        mode=args.mode,
        namespace=args.namespace,
        name=args.name,
        model="/models/Qwen3-0.6B",
        model_pvc="unused",
        image=None,
        runtime_class="nvidia" if args.platform == "cuda" else None,
        workers=2,
        prefill=2,
        decode=3,
        tp=1,
    )
)
obj["metadata"]["labels"] = {"heyi.io/campaign": args.namespace}
for component in obj["spec"]["components"]:
    pod = component["podTemplate"]["spec"]
    pod["nodeSelector"]["kubernetes.io/hostname"] = args.node
    pod["volumes"][0] = {
        "name": "models",
        "hostPath": {"path": args.model_host_path, "type": "Directory"},
    }
    container = pod["containers"][0]
    container["imagePullPolicy"] = "Never"
    container["volumeMounts"][0]["mountPath"] = "/models/Qwen3-0.6B"
    container["resources"]["limits"].update(cpu="8", memory="32Gi")
    container["env"].extend(
        [
            {"name": "PYTHONUNBUFFERED", "value": "1"},
            {"name": "GOMAXPROCS", "value": "4"},
            {"name": "SPT_NOENV", "value": "1"},
        ]
    )
    if component["type"] != "frontend":
        command = container["args"]
        command[command.index("--max-model-len") + 1] = "8192"
        command.append(
            "--enable-prefix-caching"
            if args.mode == "aggregate"
            else "--no-enable-prefix-caching"
        )
        if args.platform == "cuda":
            container["env"].append(
                {"name": "UCX_TLS", "value": "tcp,sm,self,cuda_copy,cuda_ipc"}
            )
        if args.platform == "ascend":
            for volume in pod["volumes"]:
                if volume["name"] == "ascend-smi":
                    volume["hostPath"]["path"] = "/usr/local/bin/npu-smi"
if args.second_node and args.mode == "aggregate":
    worker = next(c for c in obj["spec"]["components"] if c["name"] == "worker")
    worker["replicas"] = 1
    remote = copy.deepcopy(worker)
    remote["name"] = "remote-worker"
    remote_pod = remote["podTemplate"]["spec"]
    remote_pod["nodeSelector"]["kubernetes.io/hostname"] = args.second_node
    remote_pod["volumes"][0]["hostPath"]["path"] = args.second_model_host_path
    obj["spec"]["components"].append(remote)
elif args.second_node:
    for component in obj["spec"]["components"]:
        pod = component["podTemplate"]["spec"]
        # The base PD template deliberately co-locates all workers. Explicit
        # two-node placement must remove that constraint for both roles.
        pod.pop("affinity", None)
        if component["name"] == "decode":
            pod["nodeSelector"]["kubernetes.io/hostname"] = args.second_node
            pod["volumes"][0]["hostPath"]["path"] = args.second_model_host_path
print(json.dumps(obj, indent=2))
