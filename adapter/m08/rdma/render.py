#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render this cluster's bounded RDMA validation from the proven 2P3D manifest."""
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
source = (
    Path(__file__).resolve().parents[1]
    / "advanced/manifests"
    / a.platform
    / "pd-cross.json"
)
d = json.loads(source.read_text())
d["metadata"]["name"] = "m08-pd-rdma"
for component in d["spec"]["components"]:
    if component["type"] == "frontend":
        continue
    template = component["podTemplate"]
    template["metadata"]["labels"]["heyi.io/m08-pd"] = d["metadata"]["name"]
    c = template["spec"]["containers"][0]
    if a.platform == "cuda":
        template["metadata"].setdefault("annotations", {})[
            "k8s.v1.cni.cncf.io/networks"
        ] = "nvidia-network-operator/macvlan-roce0"
        # This cluster already advertises this shared RDMA resource. No host
        # network, privileged container or manual /dev mount is required.
        for kind in ["requests", "limits"]:
            c["resources"][kind]["nvidia.com/roce_h3cnic0"] = 1
        c.setdefault("securityContext", {}).setdefault("capabilities", {}).setdefault(
            "add", []
        ).append("IPC_LOCK")
        env = {
            "UCX_TLS": "rc,cuda_copy,cuda_ipc,sm,self",
            "UCX_NET_DEVICES": "mlx5_10:1",
            "UCX_IB_GPU_DIRECT_RDMA": "yes",
            "UCX_LOG_LEVEL": "info",
            "UCX_PROTO_INFO": "y",
        }
        c["env"] = [x for x in c["env"] if x["name"] not in env]
        c["env"] += [{"name": k, "value": v} for k, v in env.items()]
    else:
        # Retain the vendor connector's native Ascend transport. NPU RoCE
        # counters and ADXL logs, rather than this env alone, prove its path.
        c["env"] += [{"name": "ASCEND_GLOBAL_LOG_LEVEL", "value": "1"}]
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(d, indent=2) + "\n")
