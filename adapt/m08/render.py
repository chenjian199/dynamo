#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render a v1beta1 DGD for the M02 whole-device runtime images (JSON is YAML)."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = {
    "cuda": "nvidia.com/gpu",
    "metax": "metax-tech.com/gpu",
    "ascend": "huawei.com/Ascend910",
}


def render(args):
    profile = json.loads(
        (ROOT / "container/platforms" / f"{args.platform}.json").read_text()
    )
    image = args.image or f"heyi/dynamo-vllm-{args.platform}-runtime:1.5.1-m02"
    env = [
        {"name": "HF_HUB_OFFLINE", "value": "1"},
        {"name": "DYN_REQUEST_PLANE", "value": "tcp"},
        {"name": "DYN_EVENT_PLANE", "value": "zmq"},
        {"name": "DYN_ROUTER_USE_KV_EVENTS", "value": "true"},
    ]
    components = []
    roles = [("frontend", 1)]
    roles += (
        [("prefill", args.prefill), ("decode", args.decode)]
        if args.mode == "pd"
        else [("worker", args.workers)]
    )
    for role, replicas in roles:
        worker = role != "frontend"
        command = ["python", "-m", "dynamo.vllm" if worker else "dynamo.frontend"]
        if worker:
            command += [
                "--model",
                args.model,
                "--served-model-name",
                "m08-model",
                "--enforce-eager",
                "--tensor-parallel-size",
                str(args.tp),
                "--max-model-len",
                "1024",
                "--max-num-seqs",
                "8",
                "--gpu-memory-utilization",
                "0.3",
                "--block-size",
                "128",
                "--disable-custom-all-reduce",
                "--kv-events-config",
                json.dumps(
                    {
                        "publisher": "zmq",
                        "topic": "kv-events",
                        "endpoint": "tcp://127.0.0.1:20080",
                    }
                ),
            ]
            if role != "worker":
                kv = {"kv_connector": "NixlConnector", "kv_role": "kv_both"}
                if args.platform in ("ascend", "metax"):
                    kv = {
                        "kv_connector": "MooncakeConnector",
                        "kv_role": (
                            "kv_producer" if role == "prefill" else "kv_consumer"
                        ),
                    }
                    if args.platform == "metax":
                        kv["kv_connector_extra_config"] = {"mooncake_protocol": "maca"}
                    else:
                        kv.update(
                            kv_connector_module_path="vllm_ascend.distributed.kv_transfer.kv_p2p.mooncake_connector",
                            kv_port=20090,
                            kv_connector_extra_config={
                                "prefill": {"tp_size": args.tp, "dp_size": 1},
                                "decode": {"tp_size": args.tp, "dp_size": 1},
                            },
                        )
                command += [
                    "--disaggregation-mode",
                    role,
                    "--kv-transfer-config",
                    json.dumps(kv),
                ]
        else:
            command += ["--router-mode", "kv"]
        container = {
            "name": "main",
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            # Kubernetes command overrides the image ENTRYPOINT: retain SDK initialization.
            "command": ["/opt/dynamo/bin/entrypoint.sh"],
            "args": command,
            "env": env
            + [{"name": "DYN_PLATFORM", "value": args.platform if worker else "cpu"}],
            "resources": {
                "requests": {"cpu": "1", "memory": "2Gi"},
                "limits": {"memory": "16Gi"},
            },
            "volumeMounts": [
                {"name": "models", "mountPath": "/models", "readOnly": True}
            ],
        }
        pod = {
            "nodeSelector": {"kubernetes.io/arch": profile["architecture"]},
            "containers": [container],
            "volumes": [
                {
                    "name": "models",
                    "persistentVolumeClaim": {
                        "claimName": args.model_pvc,
                        "readOnly": True,
                    },
                }
            ],
        }
        if worker:
            key = RESOURCES[args.platform]
            container["resources"]["requests"][key] = args.tp
            container["resources"]["limits"][key] = args.tp
            pod["tolerations"] = [
                {"key": key, "operator": "Exists", "effect": "NoSchedule"}
            ]
            if args.runtime_class:
                pod["runtimeClassName"] = args.runtime_class
            # Pod IPs and namespaces isolate ports; never pin host device ordinals.
            container["env"] += [
                {
                    "name": "VLLM_HOST_IP",
                    "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
                },
                {"name": "VLLM_MOONCAKE_BOOTSTRAP_PORT", "value": "20090"},
                {"name": "VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT", "value": "60"},
                {"name": "ASCEND_TRANSFER_TIMEOUT", "value": "30000"},
            ]
            if args.platform == "ascend":
                for name, path, kind in [
                    ("ascend-driver", "/usr/local/Ascend/driver", "Directory"),
                    ("ascend-dcmi", "/usr/local/dcmi", "Directory"),
                    ("ascend-smi", "/usr/local/sbin/npu-smi", "File"),
                ]:
                    pod["volumes"].append(
                        {"name": name, "hostPath": {"path": path, "type": kind}}
                    )
                    container["volumeMounts"].append(
                        {"name": name, "mountPath": path, "readOnly": True}
                    )
            if args.mode == "pd":
                pod["affinity"] = {
                    "podAffinity": {
                        "requiredDuringSchedulingIgnoredDuringExecution": [
                            {
                                "labelSelector": {
                                    "matchLabels": {"heyi.io/m08-pd": args.name}
                                },
                                "topologyKey": "kubernetes.io/hostname",
                            }
                        ]
                    }
                }
        template = {"spec": pod}
        if worker and args.mode == "pd":
            template["metadata"] = {"labels": {"heyi.io/m08-pd": args.name}}
        components.append(
            {
                "name": role,
                "type": role,
                "replicas": replicas,
                "runtimeVersionOverride": "1.5.1",
                "sharedMemorySize": "8Gi",
                "podTemplate": template,
            }
        )
    return {
        "apiVersion": "nvidia.com/v1beta1",
        "kind": "DynamoGraphDeployment",
        "metadata": {
            "name": args.name,
            "namespace": args.namespace,
            "annotations": {
                "nvidia.com/workload-provider": "component",
                "nvidia.com/enable-grove": "false",
                "nvidia.com/dynamo-discovery-backend": "kubernetes",
            },
        },
        "spec": {"backendFramework": "vllm", "components": components},
    }


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=RESOURCES, required=True)
    parser.add_argument("--model-pvc", required=True)
    parser.add_argument("--model", default="/models/Qwen_Series/Qwen3-0.6B")
    parser.add_argument("--mode", choices=["aggregate", "pd"], default="aggregate")
    parser.add_argument("--name", default="m08")
    parser.add_argument("--namespace", default="dynamo-system")
    parser.add_argument("--image")
    parser.add_argument(
        "--runtime-class",
        help="Cluster-defined runtimeClass; no assumed vendor class name",
    )
    parser.add_argument("--workers", type=positive, default=2)
    parser.add_argument("--prefill", type=positive, default=2)
    parser.add_argument("--decode", type=positive, default=3)
    parser.add_argument("--tp", type=positive, default=1)
    args = parser.parse_args()
    print(json.dumps(render(args), indent=2))


if __name__ == "__main__":
    main()
