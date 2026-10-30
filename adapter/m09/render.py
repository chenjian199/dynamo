#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render the small-model M09 Planner regression on the certified M08 template."""
import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def render(platform, workdir, name="m09-small"):
    obj = json.loads(
        (ROOT / f"adapter/m08/cluster/manifests/{platform}/aggregate.json").read_text()
    )
    obj["metadata"]["name"] = name
    obj["metadata"]["labels"] = {"heyi.io/campaign": "m09-1029"}
    for component in obj["spec"]["components"]:
        pod = component["podTemplate"]["spec"]
        container = pod["containers"][0]
        if component["type"] == "worker":
            pod["terminationGracePeriodSeconds"] = 120
            container["env"].append(
                {"name": "DYN_GRACEFUL_SHUTDOWN_GRACE_PERIOD_SECS", "value": "60"}
            )
            component["name"] = "VllmDecodeWorker"
            component["replicas"] = 1
            args = container["args"]
            args[args.index("--served-model-name") + 1] = "m09-model"
            args[args.index("--max-num-seqs") + 1] = "64"
            args[args.index("--max-model-len") + 1] = "4096"
            # Small KV capacity makes the load threshold observable with a
            # bounded small-model test. This is not a throughput-optimal config.
            args += ["--kv-cache-memory-bytes", str(512 * 2**20)]
            args = [x for x in args if x != "--enable-prefix-caching"]
            args += ["--no-enable-prefix-caching"]
            container["args"] = args
        pod.setdefault("volumes", []).append(
            {
                "name": "m09-source",
                "hostPath": {"path": workdir + "/source", "type": "Directory"},
            }
        )
        container.setdefault("volumeMounts", []).append(
            {"name": "m09-source", "mountPath": "/m09-source", "readOnly": True}
        )
        container["env"].append(
            {"name": "PYTHONPATH", "value": "/m09-source/components/src"}
        )
    frontend = obj["spec"]["components"][0]
    planner_spec = copy.deepcopy(frontend["podTemplate"]["spec"])
    planner_spec.pop("runtimeClassName", None)
    planner_spec["containers"] = [
        {
            "name": "main",
            "image": f"heyi/dynamo-profiler-{platform}:1.5.1-m08-"
            + ("advanced3" if platform == "cuda" else "advanced"),
            "imagePullPolicy": "Never",
            "command": ["/opt/dynamo/venv/bin/python"],
            "args": [
                "-m",
                "dynamo.planner",
                "--config",
                json.dumps(
                    {
                        "environment": "kubernetes",
                        "backend": "vllm",
                        "mode": "agg",
                        "target_platform": platform,
                        "model_name": "m09-model",
                        "optimization_target": "load",
                        "decode_scale_up_kv_rate": 12,
                        "decode_scale_down_kv_rate": 2,
                        "max_gpu_budget": 3,
                        "min_endpoint": 1,
                        "load_adjustment_interval_seconds": 5,
                        "load_min_observations": 3,
                        "load_predictor": "constant",
                        "pre_deployment_sweeping_mode": "none",
                        "log_dir": "/tmp/m09-planner",
                        "metric_reporting_prometheus_port": 9085,
                    }
                ),
            ],
            "env": [
                {"name": "DYN_PLATFORM", "value": "cpu"},
                {"name": "TORCH_DEVICE_BACKEND_AUTOLOAD", "value": "0"},
                {"name": "PYTHONUNBUFFERED", "value": "1"},
                {
                    "name": "PYTHONPATH",
                    "value": "/m09-deps:/m09-source/components/src:/opt/dynamo/profiler-support",
                },
            ],
            "resources": {
                "requests": {"cpu": "1", "memory": "2Gi"},
                "limits": {"cpu": "4", "memory": "8Gi"},
            },
            "volumeMounts": [
                {"name": "m09-source", "mountPath": "/m09-source", "readOnly": True}
            ],
        }
    ]
    planner_spec["volumes"] = [
        v for v in planner_spec["volumes"] if v["name"] == "m09-source"
    ]
    planner_spec["volumes"].append(
        {
            "name": "m09-deps",
            "hostPath": {"path": workdir + "/deps", "type": "Directory"},
        }
    )
    planner_spec["containers"][0]["volumeMounts"].append(
        {"name": "m09-deps", "mountPath": "/m09-deps", "readOnly": True}
    )
    obj["spec"]["components"].append(
        {
            "name": "planner",
            "type": "planner",
            "replicas": 1,
            "runtimeVersionOverride": "1.5.1",
            "podTemplate": {"spec": planner_spec},
        }
    )
    return obj


def tools_pod(platform, workdir):
    spec = render(platform, workdir)["spec"]["components"][-1]["podTemplate"]["spec"]
    spec["volumes"] = [v for v in spec["volumes"] if v["name"] != "m09-deps"]
    container = spec["containers"][0]
    container["volumeMounts"] = [
        v for v in container["volumeMounts"] if v["name"] != "m09-deps"
    ]
    container["command"] = ["/bin/bash", "-lc", "sleep 21600"]
    container.pop("args")
    spec.update(
        restartPolicy="Never",
        activeDeadlineSeconds=21600,
        securityContext={"runAsUser": 0, "runAsGroup": 0},
    )
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "m09-tools",
            "namespace": "dynamo-m08-1029",
            "labels": {"heyi.io/campaign": "m09-1029"},
        },
        "spec": spec,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["cuda", "ascend"], required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--tools", action="store_true")
    parser.add_argument(
        "--sla", action="store_true", help="Exercise live FPM based SLA decisions"
    )
    args = parser.parse_args()
    obj = (tools_pod if args.tools else render)(args.platform, args.workdir)
    if args.sla:
        if args.tools:
            parser.error("sla and tools are mutually exclusive")
        obj["metadata"]["name"] = "m09-sla"
        planner = obj["spec"]["components"][-1]["podTemplate"]["spec"]["containers"][0]
        config = json.loads(planner["args"][-1])
        config.update(
            optimization_target="sla",
            enable_load_scaling=True,
            enable_throughput_scaling=False,
            metric_pulling_prometheus_endpoint=(
                "http://m09-metrics:9090"
                if args.platform == "cuda"
                else "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"
            ),
            ttft_ms=500 if args.platform == "cuda" else 1000,
            itl_ms=25 if args.platform == "cuda" else 100,
        )
        planner["args"][-1] = json.dumps(config)
    print(json.dumps(obj, indent=2))
