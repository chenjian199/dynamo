#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render M09 large-model Planner tests using previously certified engine flags."""
import argparse
import json

from render import render


def render_large(platform, workdir, model):
    obj = render(platform, workdir, name="m09-" + model)
    cuda = platform == "cuda"
    deepseek = model == "deepseek"
    tp = (4 if cuda else 8) if deepseek else 4
    model_path = (
        "/ssd/wtaobj/models/DeepSeek-V4-Flash-0731"
        if cuda and deepseek
        else (
            "/data/DeepSeek-V4-Flash-w8a8-mtp"
            if deepseek
            else (
                "/ssd/models/dynamo-regression/Qwen3-32B" if cuda else "/data/Qwen3-32B"
            )
        )
    )
    for component in obj["spec"]["components"]:
        pod = component["podTemplate"]["spec"]
        container = pod["containers"][0]
        if component["type"] == "planner":
            config = json.loads(container["args"][-1])
            config.update(max_gpu_budget=tp * 2)
            container["args"][-1] = json.dumps(config)
            continue
        for volume in pod["volumes"]:
            if volume["name"] == "models":
                volume["hostPath"]["path"] = model_path
        for mount in container["volumeMounts"]:
            if mount["name"] == "models":
                mount["mountPath"] = "/models/current"
        if component["type"] == "frontend":
            container["args"][-1] = "round-robin"
            continue
        argv = container["args"]
        pod["terminationGracePeriodSeconds"] = 240
        for entry in container["env"]:
            if entry["name"] == "DYN_GRACEFUL_SHUTDOWN_GRACE_PERIOD_SECS":
                entry["value"] = "180"
        for flag, value in {
            "--model": "/models/current",
            "--tensor-parallel-size": str(tp),
            "--max-num-seqs": "16",
            "--gpu-memory-utilization": "0.9",
            "--kv-cache-memory-bytes": str(4 * 2**30),
        }.items():
            argv[argv.index(flag) + 1] = value
        index = argv.index("--kv-events-config")
        del argv[index : index + 2]
        key = "nvidia.com/gpu" if cuda else "huawei.com/Ascend910"
        container["resources"] = {
            "requests": {key: tp, "cpu": "8", "memory": "64Gi"},
            "limits": {key: tp, "cpu": "32", "memory": "256Gi"},
        }
        component["sharedMemorySize"] = "16Gi"
        if deepseek:
            container["env"] += [
                {"name": "DYN_HEALTH_CHECK_REQUEST_TIMEOUT", "value": "120"},
                {"name": "DYN_CANARY_WAIT_TIME", "value": "30"},
            ]
            argv += [
                "--tokenizer-mode",
                "deepseek_v4",
                "--reasoning-parser",
                "deepseek_v4",
            ]
            if cuda:
                argv[argv.index("--kv-cache-memory-bytes") + 1] = str(16 * 2**30)
                argv[argv.index("--block-size") + 1] = "256"
                argv += ["--kv-cache-dtype", "fp8", "--no-enable-flashinfer-autotune"]
            else:
                index = argv.index("--kv-cache-memory-bytes")
                del argv[index : index + 2]
                argv += [
                    "--enable-expert-parallel",
                    "--quantization",
                    "ascend",
                    "--max-num-batched-tokens",
                    "4096",
                    "--additional-config",
                    '{"enable_dsa_cp":true}',
                ]
                values = {
                    "OMP_PROC_BIND": "false",
                    "OMP_NUM_THREADS": "4",
                    "PYTORCH_NPU_ALLOC_CONF": "expandable_segments:True",
                    "HCCL_BUFFSIZE": "1024",
                    "VLLM_ASCEND_ENABLE_FLASHCOMM1": "1",
                    "TASK_QUEUE_ENABLE": "1",
                    "HCCL_OP_EXPANSION_MODE": "AIV",
                    "HCCL_CONNECT_TIMEOUT": "300",
                    "HCCL_EXEC_TIMEOUT": "300",
                }
                container["env"] = [
                    e for e in container["env"] if e["name"] not in values
                ]
                container["env"] += [{"name": k, "value": v} for k, v in values.items()]
                # One TP8 replica per node. Source and model paths must exist
                # on both selected nodes before applying this manifest.
                pod["nodeSelector"].pop("kubernetes.io/hostname")
                pod["affinity"] = {
                    "nodeAffinity": {
                        "requiredDuringSchedulingIgnoredDuringExecution": {
                            "nodeSelectorTerms": [
                                {
                                    "matchExpressions": [
                                        {
                                            "key": "kubernetes.io/hostname",
                                            "operator": "In",
                                            "values": ["master-02-45d4a763", "master"],
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                }
    return obj


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["cuda", "ascend"], required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--model", choices=["qwen32", "deepseek"], required=True)
    parser.add_argument("--worker-node", help="Optional single-node placement for Qwen")
    parser.add_argument("--worker-model-path", help="Model hostPath on that node")
    args = parser.parse_args()
    obj = render_large(args.platform, args.workdir, args.model)
    if args.worker_node:
        if args.model != "qwen32" or not args.worker_model_path:
            parser.error("worker-node requires Qwen32 and worker-model-path")
        worker = next(c for c in obj["spec"]["components"] if c["type"] == "worker")
        pod = worker["podTemplate"]["spec"]
        pod["nodeSelector"]["kubernetes.io/hostname"] = args.worker_node
        next(v for v in pod["volumes"] if v["name"] == "models")["hostPath"][
            "path"
        ] = args.worker_model_path
    print(json.dumps(obj, indent=2))
