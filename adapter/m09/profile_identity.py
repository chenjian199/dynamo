#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create Profiler provenance from a captured worker; does not deploy anything."""
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--provenance", type=Path, required=True)
p.add_argument("--device-model", required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--devices-per-node", type=int)
p.add_argument("--nodes", type=int, default=1)
a = p.parse_args()
d = json.loads(a.provenance.read_text())
if a.nodes < 1 or (a.devices_per_node is not None and a.devices_per_node < 1):
    p.error("nodes and devices-per-node must be positive")
if d["platform"] == "metax" and a.devices_per_node is None:
    p.error("MetaX requires explicit --devices-per-node from the target inventory")
plugin = {"metax": "vllm-metax", "ascend": "vllm-ascend"}.get(d["platform"])
identity = dict(
    platform=d["platform"],
    device_model=a.device_model,
    driver=d["driver"],
    sdk=d["sdk"],
    engine="vllm",
    engine_version=d["packages"]["vllm"],
    plugin_version=d["packages"][plugin] if plugin else "builtin",
    model=d["model"],
    model_revision="config-sha256:" + d["model_config_sha256"],
    precision="bf16",
    parallelism={"tp": 1, "dp": 1, "pp": 1},
    kv_config={"binding": "per-candidate config.yaml SHA256"},
    topology={
        "nodes": a.nodes,
        "node": d["node"],
        "devices_per_node": a.devices_per_node or 8,
    },
    runtime_image=d["image_id"],
)
a.output.write_text(json.dumps(identity, indent=2) + "\n")
