#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render plain Kubernetes regression resources using existing node images."""

import argparse
import copy
import json
from pathlib import Path

NODES = {"cuda": ["worker06", "worker28"], "ascend": ["master-02-45d4a763", "master"]}


def pod_spec(platform, node, command, *, devices=1):
    resource = "nvidia.com/gpu" if platform == "cuda" else "huawei.com/Ascend910"
    container = {
        "name": "test",
        "image": f"docker.io/heyi/dynamo-vllm-{platform}-runtime:1.5.1-m02",
        "imagePullPolicy": "Never",
        "command": ["/opt/dynamo/bin/entrypoint.sh", *command],
        "env": [
            {"name": "DYN_PLATFORM", "value": platform},
            {"name": "HF_HUB_OFFLINE", "value": "1"},
            {"name": "PYTHONUNBUFFERED", "value": "1"},
        ],
        "resources": {
            "requests": {"cpu": "2", "memory": "4Gi"},
            "limits": {"cpu": "8", "memory": "32Gi"},
        },
        "volumeMounts": [
            {"name": "tests", "mountPath": "/regression", "readOnly": True},
            {"name": "shm", "mountPath": "/dev/shm"},
        ],
    }
    if devices:
        for key in ["requests", "limits"]:
            container["resources"][key][resource] = devices
    spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "affinity": {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchFields": [
                                {
                                    "key": "metadata.name",
                                    "operator": "In",
                                    "values": [node],
                                }
                            ]
                        }
                    ]
                }
            }
        },
        "containers": [container],
        "volumes": [
            {"name": "tests", "configMap": {"name": "regression-tests"}},
            {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "4Gi"}},
        ],
        "terminationGracePeriodSeconds": 30,
    }
    if platform == "cuda":
        spec["runtimeClassName"] = "nvidia"
    else:
        for name, path, kind in [
            ("driver", "/usr/local/Ascend/driver", "Directory"),
            ("dcmi", "/usr/local/dcmi", "Directory"),
            ("npu-smi", "/usr/local/bin/npu-smi", "File"),
            ("ascend-install", "/etc/ascend_install.info", "File"),
        ]:
            spec["volumes"].append(
                {"name": name, "hostPath": {"path": path, "type": kind}}
            )
            container["volumeMounts"].append(
                {"name": name, "mountPath": path, "readOnly": True}
            )
    return spec


def render(platform, namespace, stage="env"):
    labels = {"app.kubernetes.io/part-of": "dynamo-regression", "campaign": namespace}
    items = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace, "labels": labels},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "regression-tests",
                "namespace": namespace,
                "labels": labels,
            },
            "data": {
                name: Path(__file__).with_name(name).read_text()
                for name in (
                    "probe.py",
                    "collective.py",
                    "transfer.py",
                    "native.py",
                    "client.py",
                    "topology.py",
                    "load.py",
                    "resources.py",
                )
            },
        },
    ]
    for index, node in enumerate(NODES[platform]):
        name = f"{stage}-{index}"
        spec = pod_spec(
            platform,
            node,
            [
                "python",
                "/regression/" + ("probe.py" if stage == "env" else stage + ".py"),
            ],
            devices=2 if stage == "native" else 1,
        )
        if stage in ("collective", "transfer"):
            spec.update(hostNetwork=True, dnsPolicy="ClusterFirstWithHostNet")
            c = spec["containers"][0]
            env = {
                "RANK": str(index),
                "WORLD_SIZE": "2",
                "MASTER_PORT": "29958" if stage == "collective" else "29959",
                "MASTER_ADDR": "192.168.4.6" if platform == "cuda" else "192.168.7.7",
            }
            env["NODE_IP"] = (
                ["192.168.4.6", "192.168.4.14"]
                if platform == "cuda"
                else ["192.168.7.7", "192.168.7.5"]
            )[index]
            if platform == "cuda":
                c["securityContext"] = {"capabilities": {"add": ["IPC_LOCK"]}}
                env.update(
                    NCCL_DEBUG="INFO",
                    NCCL_NET="IB",
                    NCCL_IB_HCA="=mlx5_10",
                    NCCL_SOCKET_IFNAME="=bond0",
                )
                env.update(
                    UCX_TLS="rc,cuda_copy,cuda_ipc",
                    UCX_NET_DEVICES="mlx5_10:1",
                    UCX_LOG_LEVEL="info",
                )
                for key in ("requests", "limits"):
                    c["resources"][key]["nvidia.com/roce_h3cnic0"] = 1
            else:
                env.update(
                    HCCL_SOCKET_IFNAME="bond1",
                    HCCL_CONNECT_TIMEOUT="120",
                    HCCL_EXEC_TIMEOUT="120",
                )
            c["env"].extend({"name": k, "value": v} for k, v in env.items())
        items.append(
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": name, "namespace": namespace, "labels": labels},
                "spec": {
                    "backoffLimit": 0,
                    "activeDeadlineSeconds": 600,
                    "template": {
                        "metadata": {"labels": {**labels, "case": name}},
                        "spec": spec,
                    },
                },
            }
        )
    return {"apiVersion": "v1", "kind": "List", "items": items}


def serving(
    platform, namespace, models, *, model="Qwen3-0.6B", tp=1, prefix="regression"
):
    """models maps node names to existing host directories, never a shared PVC."""
    labels = {"app.kubernetes.io/part-of": "dynamo-regression", "campaign": namespace}
    items = []

    def meta(name):
        return {"name": name, "namespace": namespace, "labels": labels}

    def deployment(name, spec):
        spec["restartPolicy"] = "Always"
        items.append(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": meta(name),
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {"app": name}},
                    "template": {
                        "metadata": {"labels": {**labels, "app": name}},
                        "spec": spec,
                    },
                },
            }
        )

    def service(name, port):
        items.append(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": meta(name),
                "spec": {
                    "selector": {"app": name},
                    "ports": [{"port": port, "targetPort": port}],
                },
            }
        )

    node = NODES[platform][0]
    etcd = {
        "restartPolicy": "Always",
        "automountServiceAccountToken": False,
        "affinity": pod_spec(platform, node, [], devices=0)["affinity"],
        "containers": [
            {
                "name": "etcd",
                "image": "registry.k8s.io/etcd:3.6.8-0",
                "imagePullPolicy": "Never",
                "command": [
                    "etcd",
                    "--name=regression",
                    "--data-dir=/data",
                    "--listen-client-urls=http://0.0.0.0:2379",
                    "--advertise-client-urls=http://regression-etcd:2379",
                ],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "2", "memory": "1Gi"},
                },
                "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                "readinessProbe": {"tcpSocket": {"port": 2379}, "periodSeconds": 3},
            }
        ],
        "volumes": [{"name": "data", "emptyDir": {"sizeLimit": "2Gi"}}],
    }
    # Disposable discovery state is intentionally emptyDir; this is not an etcd HA test.
    deployment("regression-etcd", etcd)
    service("regression-etcd", 2379)
    env = {
        "DYN_NAMESPACE": prefix,
        "DYN_DISCOVERY_BACKEND": "etcd",
        "ETCD_ENDPOINTS": "http://regression-etcd:2379",
        "DYN_REQUEST_PLANE": "tcp",
        "DYN_EVENT_PLANE": "zmq",
        "DYN_SYSTEM_PORT": "8081",
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": "4",
    }
    front = pod_spec(
        platform,
        node,
        ["python", "-m", "dynamo.frontend", "--router-mode", "kv"],
        devices=0,
    )
    c = front["containers"][0]
    c["resources"]["requests"].update(cpu="500m", memory="1Gi")
    c["resources"]["limits"].update(cpu="4", memory="4Gi")
    c["env"] = [
        {"name": k, "value": v}
        for k, v in {
            **env,
            "DYN_PLATFORM": "cpu",
            "DYN_HTTP_PORT": "8000",
            "NVIDIA_VISIBLE_DEVICES": "void",
        }.items()
    ]
    c["readinessProbe"] = {"tcpSocket": {"port": 8000}, "periodSeconds": 5}
    deployment(f"{prefix}-frontend", front)
    service(f"{prefix}-frontend", 8000)
    for index, (worker_node, path) in enumerate(models.items()):
        if worker_node not in NODES[platform] or not path.startswith("/"):
            raise ValueError("Model requires a selected node and absolute host path")
        args = [
            "python",
            "-m",
            "dynamo.vllm",
            "--model",
            f"/models/{model}",
            "--served-model-name",
            f"{prefix}-model",
            "--enforce-eager",
            "--enable-prefix-caching",
            "--block-size",
            "128" if platform == "ascend" else "64",
            "--max-model-len",
            "4096",
            "--max-num-seqs",
            "4",
            "--kv-cache-memory-bytes",
            "1073741824",
            "--kv-events-config",
            json.dumps(
                {
                    "publisher": "zmq",
                    "topic": "kv-events",
                    "endpoint": "tcp://*:20080",
                    "enable_kv_cache_events": True,
                }
            ),
        ]
        if tp != 1:
            args.extend(["--tensor-parallel-size", str(tp)])
        spec = pod_spec(platform, worker_node, args, devices=tp)
        spec["volumes"].append(
            {"name": "model", "hostPath": {"path": path, "type": "Directory"}}
        )
        c = spec["containers"][0]
        c["volumeMounts"].append(
            {"name": "model", "mountPath": f"/models/{model}", "readOnly": True}
        )
        if model != "Qwen3-0.6B":
            c["resources"]["requests"].update(cpu="8", memory="32Gi")
            c["resources"]["limits"].update(cpu="24", memory="128Gi")
        c["env"].extend({"name": k, "value": v} for k, v in env.items())
        deployment(f"{prefix}-worker-{index}", spec)
    return {"apiVersion": "v1", "kind": "List", "items": items}


def pd_serving(platform, namespace, models, prefill, decode):
    if len(models) != 2 or not (1 <= prefill <= 8 and 1 <= decode <= 8):
        raise ValueError("PD requires both nodes and one to eight workers per role")
    base = serving(platform, namespace, models)
    items = [x for x in base["items"] if x["metadata"]["name"] == "regression-etcd"]
    for item in base["items"]:
        if item["metadata"]["name"] != "regression-frontend":
            continue
        x = copy.deepcopy(item)
        x["metadata"]["name"] = "pd-frontend"
        if x["kind"] == "Deployment":
            x["spec"]["selector"]["matchLabels"]["app"] = "pd-frontend"
            x["spec"]["template"]["metadata"]["labels"]["app"] = "pd-frontend"
            for env in x["spec"]["template"]["spec"]["containers"][0]["env"]:
                if env["name"] == "DYN_NAMESPACE":
                    env["value"] = "regression-pd"
        else:
            x["spec"]["selector"]["app"] = "pd-frontend"
        items.append(x)
    templates = [
        x
        for x in base["items"]
        if x["metadata"]["name"].startswith("regression-worker")
    ]
    index = 0
    for role, count, template, ip in [
        (
            "prefill",
            prefill,
            templates[0],
            "192.168.4.6" if platform == "cuda" else "192.168.7.7",
        ),
        (
            "decode",
            decode,
            templates[1],
            "192.168.4.14" if platform == "cuda" else "192.168.7.5",
        ),
    ]:
        for ordinal in range(count):
            x = copy.deepcopy(template)
            name = f"pd-{role}-{ordinal}"
            x["metadata"]["name"] = name
            x["spec"]["selector"]["matchLabels"]["app"] = name
            x["spec"]["template"]["metadata"]["labels"]["app"] = name
            spec = x["spec"]["template"]["spec"]
            spec.update(hostNetwork=True, dnsPolicy="ClusterFirstWithHostNet")
            c = spec["containers"][0]
            env = {e["name"]: e["value"] for e in c["env"]}
            env.update(
                DYN_NAMESPACE="regression-pd",
                DYN_SYSTEM_PORT=str(18100 + index),
                DYN_FORWARDPASS_METRIC_PORT=str(18200 + index),
                VLLM_HOST_IP=ip,
                VLLM_NIXL_SIDE_CHANNEL_PORT=str(27000 + index),
                VLLM_MOONCAKE_BOOTSTRAP_PORT=str(27100 + index),
                VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT="60",
                ASCEND_TRANSFER_TIMEOUT="30000",
            )
            command = c["command"]
            command[
                command.index("--enable-prefix-caching")
            ] = "--no-enable-prefix-caching"
            events = json.loads(command[command.index("--kv-events-config") + 1])
            events["endpoint"] = f"tcp://*:{20080 + index}"
            command[command.index("--kv-events-config") + 1] = json.dumps(events)
            kv = {"kv_connector": "NixlConnector", "kv_role": "kv_both"}
            if platform == "cuda":
                env.update(
                    UCX_TLS="rc,cuda_copy,cuda_ipc",
                    UCX_NET_DEVICES="mlx5_10:1",
                    UCX_LOG_LEVEL="info",
                )
                c["securityContext"] = {"capabilities": {"add": ["IPC_LOCK"]}}
                for key in ("requests", "limits"):
                    c["resources"][key]["nvidia.com/roce_h3cnic0"] = 1
            else:
                env.update(HCCL_SOCKET_IFNAME="bond1", GOMAXPROCS="4", SPT_NOENV="1")
                kv = {
                    "kv_connector": "MooncakeConnector",
                    "kv_connector_module_path": "vllm_ascend.distributed.kv_transfer.kv_p2p.mooncake_connector",
                    "kv_role": "kv_producer" if role == "prefill" else "kv_consumer",
                    "kv_port": 27100 + index,
                    "kv_connector_extra_config": {
                        "heyi_tp_split": False,
                        "prefill": {"tp_size": 1, "dp_size": 1},
                        "decode": {"tp_size": 1, "dp_size": 1},
                    },
                }
            command.extend(
                ["--disaggregation-mode", role, "--kv-transfer-config", json.dumps(kv)]
            )
            c["env"] = [{"name": k, "value": v} for k, v in env.items()]
            items.append(x)
            index += 1
    return {"apiVersion": "v1", "kind": "List", "items": items}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=NODES, required=True)
    parser.add_argument("--namespace", default="dynamo-regression-1028")
    parser.add_argument(
        "--stage",
        choices=["env", "collective", "transfer", "native", "serve", "pd"],
        default="env",
    )
    parser.add_argument(
        "--model-paths",
        type=json.loads,
        help="JSON object mapping node name to hostPath",
    )
    parser.add_argument("--prefill-workers", type=int, default=1)
    parser.add_argument("--decode-workers", type=int, default=1)
    parser.add_argument("--model", default="Qwen3-0.6B")
    parser.add_argument("--tp", type=int, choices=[1, 2, 4, 8], default=1)
    parser.add_argument("--prefix", default="regression")
    args = parser.parse_args()
    if args.stage in ("serve", "pd") and not args.model_paths:
        parser.error("serve requires --model-paths")
    if args.stage == "pd":
        result = pd_serving(
            args.platform,
            args.namespace,
            args.model_paths,
            args.prefill_workers,
            args.decode_workers,
        )
    elif args.stage == "serve":
        result = serving(
            args.platform,
            args.namespace,
            args.model_paths,
            model=args.model,
            tp=args.tp,
            prefix=args.prefix,
        )
    else:
        result = render(args.platform, args.namespace, args.stage)
    print(json.dumps(result, indent=2))
