#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Add test infrastructure to Profiler output; keep its measured profile intact."""
import argparse
import json
from pathlib import Path

import yaml

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--profile", type=Path, required=True)
p.add_argument("--assembly", default="final_config.yaml")
p.add_argument("--input", type=Path, required=True)
p.add_argument("--workdir", required=True)
p.add_argument("--source-dir", default="source")
p.add_argument("--worker-source-dir")
p.add_argument("--health-timeout", type=int, default=0)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
inputs = json.loads(a.input.read_text())
platform = inputs["identity"]["platform"]
objects = list(yaml.safe_load_all((a.profile / a.assembly).read_text()))
for obj in objects:
    obj["metadata"]["namespace"] = "dynamo-m08-1029"
    obj["metadata"].setdefault("labels", {})["heyi.io/campaign"] = "m09-offline-1030"
    if obj["kind"] == "ConfigMap" and "planner_config.json" in obj["data"]:
        config = json.loads(obj["data"]["planner_config.json"])
        config.update(
            enable_load_scaling=False,
            enable_throughput_scaling=True,
            throughput_adjustment_interval_seconds=15,
            load_adjustment_interval_seconds=5,
            load_min_observations=3,
            load_predictor="constant",
            isl=512,
            osl=128,
            log_dir="/tmp/m09-offline-planner",
            metric_reporting_prometheus_port=9085,
            metric_pulling_prometheus_endpoint=(
                "http://m09-metrics:9090"
                if platform == "cuda"
                else "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"
            ),
        )
        obj["data"]["planner_config.json"] = json.dumps(config)
    if obj["kind"] != "DynamoGraphDeployment":
        continue
    obj["metadata"]["name"] = inputs["dgd"]["metadata"]["name"]
    for comp in obj["spec"]["components"]:
        pod = comp["podTemplate"]["spec"]
        c = pod["containers"][0]
        # File-only mode does not need an additional in-worker self benchmark.
        c["env"] = [e for e in c.get("env", []) if e["name"] != "DYN_BENCHMARK_MODE"]
        if comp["type"] != "planner":
            if comp["type"] == "decode" and not any(
                e["name"] == "DYN_HEALTH_CHECK_PAYLOAD" for e in c["env"]
            ):
                c["env"].append(
                    {
                        "name": "DYN_HEALTH_CHECK_PAYLOAD",
                        "value": json.dumps(
                            {
                                "token_ids": [1],
                                "sampling_options": {"temperature": 0.0},
                                "stop_conditions": {"max_tokens": 1},
                                "annotations": ["x-bypass-remote-prefill"],
                            }
                        ),
                    }
                )
            if a.health_timeout and comp["type"] != "frontend":
                overrides = {
                    "DYN_HEALTH_CHECK_REQUEST_TIMEOUT": str(a.health_timeout),
                    "DYN_CANARY_WAIT_TIME": "30",
                    "DYN_GRACEFUL_SHUTDOWN_GRACE_PERIOD_SECS": "180",
                }
                c["env"] = [e for e in c["env"] if e["name"] not in overrides]
                c["env"] += [{"name": k, "value": v} for k, v in overrides.items()]
                pod["terminationGracePeriodSeconds"] = 240
            if a.worker_source_dir:
                for volume in pod.get("volumes", []):
                    if volume["name"] == "m09-source":
                        volume["hostPath"]["path"] = (
                            a.workdir + "/" + a.worker_source_dir
                        )
            continue
        comp["name"] = "planner"
        comp["runtimeVersionOverride"] = "1.5.1"
        pod["nodeSelector"] = inputs["dgd"]["spec"]["components"][0]["podTemplate"][
            "spec"
        ]["nodeSelector"]
        c["image"] = inputs["dgdr"]["image"]
        c["imagePullPolicy"] = "Never"
        c["command"] = ["/opt/dynamo/venv/bin/python"]
        # Preserve the profile/config mounts and their paths from Profiler.
        config_path = c["args"][c["args"].index("--config") + 1]
        c["args"] = ["-m", "dynamo.planner", "--config", config_path]
        c["env"] = [
            {"name": "DYN_PLATFORM", "value": "cpu"},
            {"name": "TORCH_DEVICE_BACKEND_AUTOLOAD", "value": "0"},
            {"name": "PYTHONUNBUFFERED", "value": "1"},
            {
                "name": "PYTHONPATH",
                "value": "/m09-deps:/m09-source/components/src:/opt/dynamo/profiler-support",
            },
        ]
        for name, host, mount in [
            ("m09-source", a.workdir + "/" + a.source_dir, "/m09-source"),
            ("m09-deps", a.workdir + "/deps", "/m09-deps"),
        ]:
            pod.setdefault("volumes", []).append(
                {"name": name, "hostPath": {"path": host, "type": "Directory"}}
            )
            c.setdefault("volumeMounts", []).append(
                {"name": name, "mountPath": mount, "readOnly": True}
            )
        c["resources"] = {
            "requests": {"cpu": "1", "memory": "2Gi"},
            "limits": {"cpu": "4", "memory": "8Gi"},
        }
a.output.write_text(yaml.safe_dump_all(objects, sort_keys=False))
