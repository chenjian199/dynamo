#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deploy measured final-image profiles without development source overlays."""

import argparse
import json
import re
from pathlib import Path

import yaml

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--profile", type=Path, required=True)
p.add_argument("--input", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--runtime-version", required=True)
a = p.parse_args()
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", a.runtime_version):
    p.error("Supply the measured stable Dynamo runtime version")
inputs = json.loads(a.input.read_text())
namespace = inputs["dgd"]["metadata"]["namespace"]
platform = inputs["identity"]["platform"]
objects = list(yaml.safe_load_all((a.profile / "final_config-reused.yaml").read_text()))
for obj in objects:
    obj["metadata"]["namespace"] = namespace
    obj["metadata"].setdefault("labels", {})["heyi.io/campaign"] = namespace
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
            log_dir="/tmp/m10-offline-planner",
            metric_reporting_prometheus_port=9085,
            metric_pulling_prometheus_endpoint=(
                "http://m10-metrics:9090"
                if platform == "cuda"
                else "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"
            ),
        )
        obj["data"]["planner_config.json"] = json.dumps(config)
    if obj["kind"] != "DynamoGraphDeployment":
        continue
    obj["metadata"]["name"] = inputs["dgd"]["metadata"]["name"]
    for comp in obj["spec"]["components"]:
        comp["runtimeVersionOverride"] = a.runtime_version
        pod = comp["podTemplate"]["spec"]
        c = pod["containers"][0]
        c["env"] = [e for e in c.get("env", []) if e["name"] != "DYN_BENCHMARK_MODE"]
        if comp["type"] != "planner":
            continue
        comp.update(name="planner")
        pod["nodeSelector"] = inputs["dgd"]["spec"]["components"][0]["podTemplate"][
            "spec"
        ]["nodeSelector"]
        c["image"] = inputs["dgdr"]["image"]
        c["imagePullPolicy"] = "Never"
        c["command"] = [
            (
                "/opt/dynamo/bin/tools-entrypoint.sh"
                if platform == "ascend"
                else "/opt/dynamo/bin/entrypoint.sh"
            )
        ]
        config_path = c["args"][c["args"].index("--config") + 1]
        c["args"] = ["python", "-m", "dynamo.planner", "--config", config_path]
        c["env"] = [
            {"name": k, "value": v}
            for k, v in {
                "DYN_PLATFORM": "cpu",
                "TORCH_DEVICE_BACKEND_AUTOLOAD": "0",
                "PYTHONUNBUFFERED": "1",
            }.items()
        ]
        c["resources"] = {
            "requests": {"cpu": "1", "memory": "2Gi"},
            "limits": {"cpu": "4", "memory": "8Gi"},
        }
encoded = yaml.safe_dump_all(objects, sort_keys=False)
if any(
    x in encoded for x in ["/m09-source", "/m09-deps", "site-packages/", "PYTHONPATH"]
):
    raise ValueError("Implementation overlay found in final configuration")
a.output.write_text(encoded)
