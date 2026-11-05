#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render source-free DGDR, capacity, metrics and CPU load-generator manifests."""
import sys
from pathlib import Path

# Resolve shared helpers from their functional groups for direct script execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rendering'))

import argparse
import importlib.util
import json
import subprocess

from render import ROOT

spec = importlib.util.spec_from_file_location(
    "offline", Path(__file__).with_name("render-offline.py")
)
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--namespace", default="dynamo-m10")
p.add_argument("--runtime-image", required=True)
p.add_argument("--tools-image", required=True)
p.add_argument("--workdir", required=True)
p.add_argument("--devices-per-node", type=int, required=True)
p.add_argument("--vram-mib", type=int, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
if min(a.devices_per_node, a.vram_mib) <= 0:
    p.error("Use measured positive node capacities")
a.output.mkdir(parents=True, exist_ok=True)
sku = "h800_sxm" if a.platform == "cuda" else "ascend_910b3"
capacity = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "m10-capacity", "namespace": a.namespace},
    "data": {
        sku
        + ".yaml": json.dumps(
            {
                "node": {"num_gpus_per_node": a.devices_per_node},
                "gpu": {"mem_capacity": a.vram_mib * 2**20},
            }
        )
    },
}
dgdr = json.loads(
    (ROOT / f"adapter/modules/m08-kubernetes/common/advanced/manifests/{a.platform}/dgdr.json").read_text()
)
dgdr = offline.clean(dgdr, a.namespace, a.runtime_image, a.tools_image)
dgdr["metadata"].update(
    name="m10-profile", namespace=a.namespace, labels={"heyi.io/campaign": a.namespace}
)
dgdr["spec"].update(image=a.tools_image, autoApply=False)
dgdr["spec"]["hardware"].update(vramMb=a.vram_mib, numGpusPerNode=a.devices_per_node)
pod = dgdr["spec"]["overrides"]["profilingJob"]["template"]["spec"]
pod["securityContext"] = {"runAsUser": 0, "runAsGroup": 0, "runAsNonRoot": False}
pod["volumes"].append({"name": "capacity", "configMap": {"name": "m10-capacity"}})
for volume in pod["volumes"]:
    if volume["name"] == "profiling-output":
        volume["hostPath"]["path"] = a.workdir + "/evidence/profiler"
for c in pod["containers"]:
    c["image"] = a.tools_image
    environment = {e["name"]: e for e in c.get("env", [])}
    environment.update(
        {
            "DYN_PLATFORM": {"name": "DYN_PLATFORM", "value": "cpu"},
            "TORCH_DEVICE_BACKEND_AUTOLOAD": {
                "name": "TORCH_DEVICE_BACKEND_AUTOLOAD",
                "value": "0",
            },
        }
    )
    c["env"] = list(environment.values())
    c.setdefault("volumeMounts", []).append(
        {
            "name": "capacity",
            "mountPath": "/opt/dynamo/platform-capacity",
            "readOnly": True,
        }
    )
tools = json.loads(
    subprocess.check_output(
        [
            sys.executable,
            str(ROOT / "adapter/modules/m09-telemetry-planner/common/rendering/render.py"),
            "--platform",
            a.platform,
            "--workdir",
            a.workdir,
            "--tools",
        ]
    )
)
tools = offline.clean(tools, a.namespace, a.runtime_image, a.tools_image)
tools["metadata"].update(name="m10-tools", labels={"heyi.io/campaign": a.namespace})
tools["spec"]["containers"][0]["command"] = ["/bin/bash", "-c", "sleep 21600"]
metrics = json.loads(
    subprocess.check_output(
        [
            sys.executable,
            str(ROOT / "adapter/modules/m09-telemetry-planner/common/rendering/render_metrics.py"),
            "--platform",
            a.platform,
        ]
    )
)
metrics = json.loads(
    json.dumps(metrics).replace("dynamo-m08-1029", a.namespace).replace("m09-", "m10-")
)
# Include the actual final-image campaign names, not only the M09 aliases.
extra_deployments = [
    "m10-small",
    "m10-offline-r1",
    "m10-offline-r1-qwen32",
    "m10-offline-r1-deepseek",
    "m10-offline-r3-deepseek",
    "m10-offline-r3-deepseek-expanded",
    "m10-hybrid-r3",
]
if metrics["kind"] == "PodMonitor":
    metrics["spec"]["selector"]["matchExpressions"][0]["values"].extend(
        extra_deployments
    )
else:
    config_map = next(x for x in metrics["items"] if x["kind"] == "ConfigMap")
    scrape_config = json.loads(config_map["data"]["prometheus.yml"])
    selector = scrape_config["scrape_configs"][0]["relabel_configs"][0]
    selector["regex"] += "|" + "|".join(extra_deployments)
    config_map["data"]["prometheus.yml"] = json.dumps(scrape_config)
for name, obj in [
    ("capacity", capacity),
    ("dgdr", dgdr),
    ("tools", tools),
    ("metrics", metrics),
]:
    encoded = json.dumps(obj, indent=2)
    if any(
        s in encoded
        for s in [
            "/m09-source",
            "/m09-deps",
            "site-packages/",
            "1.5.1-m02",
            "1.5.1-m08",
            "m08-profiler-source",
            "m08-preflight-source",
        ]
    ):
        raise ValueError(f"Stale source overlay in {name}")
    (a.output / f"{name}.json").write_text(encoded + "\n")
print(a.output)
