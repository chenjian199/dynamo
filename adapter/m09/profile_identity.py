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
a = p.parse_args()
d = json.loads(a.provenance.read_text())
identity = dict(
    platform=d["platform"],
    device_model=a.device_model,
    driver=d["driver"],
    sdk=d["sdk"],
    engine="vllm",
    engine_version=d["packages"]["vllm"],
    plugin_version=d["packages"].get("vllm-ascend", "builtin"),
    model=d["model"],
    model_revision="config-sha256:" + d["model_config_sha256"],
    precision="bf16",
    parallelism={"tp": 1, "dp": 1, "pp": 1},
    kv_config={"binding": "per-candidate config.yaml SHA256"},
    topology={"nodes": 1, "node": d["node"], "devices_per_node": 8},
    runtime_image=d["image_id"],
)
a.output.write_text(json.dumps(identity, indent=2) + "\n")
