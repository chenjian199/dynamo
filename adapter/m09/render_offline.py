#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render bounded real-device offline sweeps from the certified PD template."""
import argparse
import copy
import json
from pathlib import Path

from render import tools_pod

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--workdir", required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
dgd = json.loads(
    (ROOT / f"adapter/m08/advanced/manifests/{a.platform}/pd-cross.json").read_text()
)
dgd["metadata"]["name"] = "m09-offline"
dgd["metadata"]["labels"] = {"heyi.io/campaign": "m09-offline-1030"}
node = "worker06" if a.platform == "cuda" else "master-02-45d4a763"
model = (
    "/ssd/models/dynamo-regression/Qwen3-0.6B"
    if a.platform == "cuda"
    else "/data/weight/Qwen3-0.6B"
)
for comp in dgd["spec"]["components"]:
    comp["replicas"] = 1
    pod = comp["podTemplate"]["spec"]
    pod["nodeSelector"]["kubernetes.io/hostname"] = node
    pod["terminationGracePeriodSeconds"] = 120
    c = pod["containers"][0]
    for v in pod["volumes"]:
        if v["name"] == "models":
            v["hostPath"]["path"] = model
    pod["volumes"].append(
        {
            "name": "m09-source",
            "hostPath": {"path": a.workdir + "/source", "type": "Directory"},
        }
    )
    c.setdefault("volumeMounts", []).append(
        {"name": "m09-source", "mountPath": "/m09-source", "readOnly": True}
    )
    c["env"] += [
        {"name": "PYTHONPATH", "value": "/m09-source/components/src"},
        {"name": "DYN_GRACEFUL_SHUTDOWN_GRACE_PERIOD_SECS", "value": "60"},
    ]
    if comp["type"] != "frontend":
        args = c["args"]
        for key, val in [
            ("--served-model-name", "m09-model"),
            ("--max-model-len", "4096"),
            ("--max-num-seqs", "8"),
        ]:
            args[args.index(key) + 1] = val
        args += ["--kv-cache-memory-bytes", str(512 * 2**20)]
        # Stable component names recognized by generated Planner adapters.
        comp["name"] = (
            "VllmPrefillWorker" if comp["type"] == "prefill" else "VllmDecodeWorker"
        )
    else:
        comp["name"] = "Frontend"
dgdr = json.loads(
    (ROOT / f"adapter/m08/advanced/manifests/{a.platform}/dgdr.json").read_text()
)["spec"]
dgdr["features"] = {
    "planner": {
        "target_platform": a.platform,
        "mode": "disagg",
        "backend": "vllm",
        "model_name": "m09-model",
        "optimization_target": "sla",
        "pre_deployment_sweeping_mode": "thorough",
        "pre_deployment_metrics_source": "files",
        "enable_throughput_scaling": True,
        "enable_load_scaling": True,
        "max_gpu_budget": 4,
        "min_endpoint": 1,
        "ttft_ms": 1000,
        "itl_ms": 50 if a.platform == "cuda" else 150,
    }
}
identity = json.loads(
    (
        ROOT
        / f"adapter/m09/results/{a.platform}/profile/profiler/prefill_1gpus_tp1/profile-identity.json"
    ).read_text()
)["identity"]
identity["parallelism"] = {"prefill_tp": 1, "decode_tp": 1}
identity["kv_config"] = {
    "cache_bytes": 512 * 2**20,
    "block_size": 128,
    "max_model_len": 4096,
    "max_num_seqs": 8,
}
dgdr["features"]["planner"]["profile_identity"] = identity
(a.output / "input.json").write_text(
    json.dumps({"dgd": dgd, "dgdr": dgdr, "identity": identity}, indent=2) + "\n"
)
tools = tools_pod(a.platform, a.workdir)
tools["metadata"]["name"] = "m09-offline-tools"
spec = tools["spec"]
spec["serviceAccountName"] = "m09-offline-tools"
c = spec["containers"][0]
spec["volumes"] += [
    {"name": "offline-work", "hostPath": {"path": a.workdir, "type": "Directory"}},
    {"name": "models", "hostPath": {"path": model, "type": "Directory"}},
    {
        "name": "m09-deps",
        "hostPath": {"path": a.workdir + "/deps", "type": "Directory"},
    },
]
c["volumeMounts"] += [
    {"name": "offline-work", "mountPath": "/offline"},
    {"name": "models", "mountPath": "/models/Qwen3-0.6B", "readOnly": True},
    {"name": "m09-deps", "mountPath": "/m09-deps", "readOnly": True},
]
c["env"] += [{"name": "K3S_DATA_DIR", "value": "/tmp/m09-k3s"}]
# Reuse the certified tokenizer compatibility mount from the M08 Profiler.
old = json.loads(
    (ROOT / f"adapter/m08/advanced/manifests/{a.platform}/dgdr.json").read_text()
)["spec"]["overrides"]["profilingJob"]["template"]["spec"]
for v in old["volumes"]:
    if v["name"] in ["profiler-tokenizer", "deployment-client"]:
        spec["volumes"].append(copy.deepcopy(v))
for mount in old["containers"][0]["volumeMounts"]:
    if mount["name"] in ["profiler-tokenizer", "deployment-client"]:
        c["volumeMounts"].append(copy.deepcopy(mount))
(a.output / "tools.json").write_text(json.dumps(tools, indent=2) + "\n")

meta = {"name": "m09-offline-tools", "namespace": "dynamo-m08-1029"}
rbac = {
    "apiVersion": "v1",
    "kind": "List",
    "items": [
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": meta},
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": meta,
            "rules": [
                {
                    "apiGroups": ["nvidia.com"],
                    "resources": ["dynamographdeployments"],
                    "verbs": ["get", "list", "watch", "create", "delete", "patch"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods", "pods/log", "services", "events"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["configmaps"],
                    "verbs": ["get", "list", "watch", "create", "delete", "patch"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": meta,
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": meta["name"],
            },
            "subjects": [{"kind": "ServiceAccount", **meta}],
        },
    ],
}
(a.output / "rbac.json").write_text(json.dumps(rbac, indent=2) + "\n")
