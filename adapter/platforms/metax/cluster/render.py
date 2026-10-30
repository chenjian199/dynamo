#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render MetaX cluster inputs without connecting to Kubernetes or using GPUs."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "m08_render", ROOT / "adapter/m08/render.py"
)
m08 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m08)
RESOURCE = "metax-tech.com/gpu"


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def validate(config):
    for key in (
        "name",
        "namespace",
        "runtime_image",
        "tools_image",
        "prefill_node",
        "decode_node",
        "model",
    ):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"{key} must be explicitly provided")
    if config["prefill_node"] == config["decode_node"]:
        raise ValueError(
            "Cross-node PD requires different prefill_node and decode_node"
        )
    if not config["model"].startswith("/models/"):
        raise ValueError("model must be below the /models mount")
    if bool(config.get("model_pvc")) == bool(config.get("model_host_path")):
        raise ValueError("Specify exactly one of model_pvc and model_host_path")
    if config.get("model_host_path") and not config["model_host_path"].startswith("/"):
        raise ValueError("model_host_path must be absolute and exist on both nodes")
    for key in ("devices_per_node", "vram_mb", "gpu_budget"):
        positive(config.get(key), key)
    for key in ("prefill_tp", "decode_tp", "prefill_replicas", "decode_replicas"):
        positive(config.get(key, 1), key)
    if config["gpu_budget"] > config["devices_per_node"] * 2:
        raise ValueError("gpu_budget exceeds the two declared nodes")
    for key in ("prefill_tp", "decode_tp"):
        if config.get(key, 1) > config["devices_per_node"]:
            raise ValueError(
                f"{key} exceeds one node; multi-node TP needs a distributed executor"
            )
    # These are placement feasibility checks, not a tested TP combination whitelist.
    for role in ("prefill", "decode"):
        if (
            config.get(role + "_tp", 1) * config.get(role + "_replicas", 1)
            > config["devices_per_node"]
        ):
            raise ValueError(f"{role} replicas exceed their pinned node capacity")
    for key in ("isl", "osl", "max_model_len", "max_num_seqs", "block_size"):
        positive(
            config.get(
                key,
                {
                    "isl": 128,
                    "osl": 16,
                    "max_model_len": 4096,
                    "max_num_seqs": 8,
                    "block_size": 128,
                }[key],
            ),
            key,
        )
    network = config.get("network_env", {})
    allowed_network = {
        "MCCL_SOCKET_IFNAME",
        "GLOO_SOCKET_IFNAME",
        "UCX_NET_DEVICES",
        "UCX_TLS",
        "UCX_PROTO_ENABLE",
    }
    if not isinstance(network, dict) or set(network) - allowed_network:
        raise ValueError("network_env accepts only MCCL/GLOO/UCX transport settings")
    if config.get("planner_files") and not config.get("identity"):
        raise ValueError("planner_files requires a captured MetaX profile identity")
    if config.get("planner_files") and not config.get("prometheus_endpoint"):
        raise ValueError("planner_files requires an explicit Prometheus endpoint")
    if config.get("identity"):
        identity = config["identity"]
        if (
            identity.get("platform") != "metax"
            or identity.get("engine_version") != "0.26.0"
        ):
            raise ValueError("identity must describe MetaX vLLM 0.26.0")
        if identity.get("model") != config["model"]:
            raise ValueError("identity model must match the mounted model path")
    if config.get("planner_files") and config["gpu_budget"] < 2:
        raise ValueError("Disaggregated Planner needs at least two devices")


def pod(component):
    return component["podTemplate"]["spec"]


def main_container(component):
    return pod(component)["containers"][0]


def cpu_container(image):
    return {
        "name": "main",
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["/opt/dynamo/venv/bin/python"],
        "workingDir": "/tmp",
        "env": [
            {"name": "DYN_PLATFORM", "value": "cpu"},
            {"name": "TORCH_DEVICE_BACKEND_AUTOLOAD", "value": "0"},
            {"name": "HF_HUB_OFFLINE", "value": "1"},
        ],
        "resources": {
            "requests": {"cpu": "1", "memory": "2Gi"},
            "limits": {"cpu": "4", "memory": "8Gi"},
        },
    }


def deployment(config, mode):
    args = SimpleNamespace(
        platform="metax",
        image=config["runtime_image"],
        model=config["model"],
        model_pvc=config.get("model_pvc"),
        mode=mode,
        tp=1,
        workers=2,
        prefill=config.get("prefill_replicas", 1),
        decode=config.get("decode_replicas", 1),
        runtime_class=config.get("runtime_class"),
        name=config["name"] + "-" + mode,
        namespace=config["namespace"],
    )
    dgd = m08.render(args)
    names = {
        "frontend": "Frontend",
        "worker": "VllmDecodeWorker",
        "prefill": "VllmPrefillWorker",
        "decode": "VllmDecodeWorker",
    }
    for component in dgd["spec"]["components"]:
        role = component["type"]
        component["name"] = names[role]
        p, c = pod(component), main_container(component)
        p.pop("affinity", None)
        component["podTemplate"].pop("metadata", None)
        p["nodeSelector"]["kubernetes.io/hostname"] = (
            config["decode_node"]
            if role in {"decode", "worker"}
            else config["prefill_node"]
        )
        if config.get("model_host_path"):
            p["volumes"] = [
                {
                    "name": "models",
                    "hostPath": {
                        "path": config["model_host_path"],
                        "type": "Directory",
                    },
                }
            ]
        if role == "frontend":
            c["env"].append({"name": "TORCH_DEVICE_BACKEND_AUTOLOAD", "value": "0"})
            continue
        p["terminationGracePeriodSeconds"] = 240
        tp = config.get("prefill_tp" if role == "prefill" else "decode_tp", 1)
        for field in ("limits", "requests"):
            c["resources"][field][RESOURCE] = tp
        for flag, value in {
            "--tensor-parallel-size": tp,
            "--served-model-name": "metax-model",
            "--max-model-len": config.get("max_model_len", 4096),
            "--max-num-seqs": config.get("max_num_seqs", 8),
            "--block-size": config.get("block_size", 128),
        }.items():
            c["args"][c["args"].index(flag) + 1] = str(value)
        c["env"] = [e for e in c["env"] if not e["name"].startswith("ASCEND_")]
        c["env"].append(
            {"name": "DYN_GRACEFUL_SHUTDOWN_GRACE_PERIOD_SECS", "value": "180"}
        )
        # Interface/device names must come from the target network inventory.
        c["env"] += [
            {"name": key, "value": str(value)}
            for key, value in config.get("network_env", {}).items()
        ]
    if mode == "aggregate":
        worker_pod = pod(dgd["spec"]["components"][1])
        worker_pod["nodeSelector"].pop("kubernetes.io/hostname")
        worker_pod["affinity"] = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": "kubernetes.io/hostname",
                                    "operator": "In",
                                    "values": [
                                        config["prefill_node"],
                                        config["decode_node"],
                                    ],
                                }
                            ]
                        }
                    ]
                }
            }
        }
    return dgd


def render(config):
    validate(config)
    aggregate, pd = deployment(config, "aggregate"), deployment(config, "pd")
    grove = copy.deepcopy(aggregate)
    grove["metadata"]["name"] = config["name"] + "-grove"
    grove["metadata"]["annotations"].update(
        {"nvidia.com/workload-provider": "grove", "nvidia.com/enable-grove": "true"}
    )
    if config.get("scheduler_name"):
        for component in grove["spec"]["components"]:
            pod(component)["schedulerName"] = config["scheduler_name"]
    planner = copy.deepcopy(aggregate)
    planner["metadata"]["name"] = config["name"] + "-planner"
    planner["spec"]["components"][1]["replicas"] = 1
    pc = cpu_container(config["tools_image"])
    pc["args"] = [
        "-m",
        "dynamo.planner",
        "--config",
        json.dumps(
            {
                "environment": "kubernetes",
                "backend": "vllm",
                "mode": "agg",
                "target_platform": "metax",
                "model_name": "metax-model",
                "optimization_target": "load",
                "decode_scale_up_kv_rate": 12,
                "decode_scale_down_kv_rate": 2,
                "max_gpu_budget": config["gpu_budget"],
                "min_endpoint": 1,
                "pre_deployment_sweeping_mode": "none",
                "load_predictor": "constant",
                "load_min_observations": 3,
                "log_dir": "/tmp/metax-planner",
                "metric_reporting_prometheus_port": 9085,
            }
        ),
    ]
    planner["spec"]["components"].append(
        {
            "name": "Planner",
            "type": "planner",
            "replicas": 1,
            "runtimeVersionOverride": "1.5.1",
            "podTemplate": {
                "spec": {
                    "nodeSelector": copy.deepcopy(
                        pod(aggregate["spec"]["components"][0])["nodeSelector"]
                    ),
                    "containers": [pc],
                }
            },
        }
    )
    # Overrides intentionally omit fixed worker arguments/device counts: the
    # Profiler owns candidate TP/DP/PP, connector shapes and resource requests.
    override = copy.deepcopy(pd)
    override["metadata"].pop("name")
    for component in override["spec"]["components"]:
        component.pop("replicas")
        c = main_container(component)
        c.pop("args")
        c["command"] = [
            "/opt/dynamo/bin/entrypoint.sh",
            "python",
            "-m",
            "dynamo.frontend" if component["type"] == "frontend" else "dynamo.vllm",
        ]
        for field in ("limits", "requests"):
            c["resources"][field].pop(RESOURCE, None)
    # Match the established Profiler override contract: engine args select PD roles.
    for component in override["spec"]["components"]:
        if component["type"] != "frontend":
            component["type"] = "worker"
    capacity_name = config["name"] + "-profile-inputs"
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": capacity_name, "namespace": config["namespace"]},
        "data": {
            "metax_c500.yaml": json.dumps(
                {
                    "node": {"num_gpus_per_node": config["devices_per_node"]},
                    "gpu": {"mem_capacity": config["vram_mb"] * 2**20},
                }
            ),
        },
    }
    job = copy.deepcopy(pod(aggregate["spec"]["components"][0]))
    prof = cpu_container(config["tools_image"])
    prof["name"] = "profiler"
    prof.pop("command")  # Keep the Operator's profiler invocation and output path.
    prof["volumeMounts"] = copy.deepcopy(
        main_container(aggregate["spec"]["components"][0])["volumeMounts"]
    )
    prof["volumeMounts"].append(
        {
            "name": "profile-inputs",
            "mountPath": "/opt/dynamo/platform-capacity",
            "readOnly": True,
        }
    )
    job["volumes"].append(
        {"name": "profile-inputs", "configMap": {"name": capacity_name}}
    )
    job["containers"] = [
        prof,
        {
            "name": "output-copier",
            "image": config["tools_image"],
            "imagePullPolicy": "IfNotPresent",
        },
    ]
    dgdr = {
        "apiVersion": "nvidia.com/v1beta1",
        "kind": "DynamoGraphDeploymentRequest",
        "metadata": {
            "name": config["name"] + "-profile",
            "namespace": config["namespace"],
        },
        "spec": {
            "model": config["model"],
            "backend": "vllm",
            "image": config["tools_image"],
            "runtimeVersionOverride": "1.5.1",
            "searchStrategy": "thorough",
            "autoApply": False,
            "hardware": {
                "gpuSku": "metax_c500",
                "vramMb": config["vram_mb"],
                "numGpusPerNode": config["devices_per_node"],
                "totalGpus": config["gpu_budget"],
            },
            "workload": {
                "isl": config.get("isl", 128),
                "osl": config.get("osl", 16),
                "concurrency": 1,
            },
            "sla": {
                "ttft": config.get("ttft_ms", 1000),
                "itl": config.get("itl_ms", 100),
            },
            "overrides": {
                "dgd": override,
                "profilingJob": {
                    "backoffLimit": 0,
                    "activeDeadlineSeconds": config.get("profiling_timeout", 7200),
                    "template": {"spec": job},
                },
            },
        },
    }
    if config.get("identity"):
        cm["data"]["identity.json"] = json.dumps(config["identity"])
        prof["env"].append(
            {
                "name": "DYN_PROFILE_IDENTITY",
                "value": "/opt/dynamo/platform-capacity/identity.json",
            }
        )
    if config.get("planner_files"):
        dgdr["spec"]["features"] = {
            "planner": {
                "target_platform": "metax",
                "backend": "vllm",
                "mode": "disagg",
                "optimization_target": "sla",
                "pre_deployment_sweeping_mode": "thorough",
                "pre_deployment_metrics_source": "files",
                "profile_identity": config["identity"],
                "max_gpu_budget": config["gpu_budget"],
                "min_endpoint": 1,
                "metric_pulling_prometheus_endpoint": config["prometheus_endpoint"],
            }
        }
        # Apply after final assembly; retain generated --config and profile mounts.
        pc = cpu_container(config["tools_image"])
        pc["command"] += ["-m", "dynamo.planner"]
        override["spec"]["components"].append(
            {
                "name": "Planner",
                "podTemplate": {
                    "spec": {
                        "nodeSelector": copy.deepcopy(job["nodeSelector"]),
                        "containers": [pc],
                    }
                },
            }
        )
    return {
        "aggregate.json": aggregate,
        "pd-cross.json": pd,
        "grove.json": grove,
        "planner-load.json": planner,
        "profile-inputs.json": cm,
        "dgdr.json": dgdr,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifacts = render(json.loads(args.config.read_text()))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")
        (args.output / (name + ".license")).write_text(
            "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\n"
        )


if __name__ == "__main__":
    main()
