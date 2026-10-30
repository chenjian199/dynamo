#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reuse the offline test contract with the previously verified large-model engine flags."""
import argparse
import copy
import json
from pathlib import Path

from render_large import render_large

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--model", choices=["qwen32", "deepseek"], required=True)
p.add_argument("--small-input", type=Path, required=True)
p.add_argument("--small-tools", type=Path, required=True)
p.add_argument("--workdir", required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
data = json.loads(a.small_input.read_text())
tools = json.loads(a.small_tools.read_text())
agg = render_large(a.platform, a.workdir, a.model)
worker = next(c for c in agg["spec"]["components"] if c["type"] == "worker")
frontend = next(c for c in agg["spec"]["components"] if c["type"] == "frontend")
tp = 4 if a.model == "qwen32" or a.platform == "cuda" else 8
if a.model == "qwen32":
    pod = worker["podTemplate"]["spec"]
    pod["nodeSelector"]["kubernetes.io/hostname"] = (
        "worker28" if a.platform == "cuda" else "master"
    )
    next(v for v in pod["volumes"] if v["name"] == "models")["hostPath"]["path"] = (
        "/root/Qwen3-32B" if a.platform == "cuda" else "/data/Qwen3-32B"
    )
    args = pod["containers"][0]["args"]
    args[args.index("--kv-cache-memory-bytes") + 1] = str(512 * 2**20)
components = [frontend]
for role in ["prefill", "decode"]:
    c = copy.deepcopy(worker)
    c["name"] = "VllmPrefillWorker" if role == "prefill" else "VllmDecodeWorker"
    c["type"] = role
    c["replicas"] = 1
    if a.model == "deepseek" and a.platform == "ascend":
        c["podTemplate"]["spec"]["nodeSelector"]["kubernetes.io/hostname"] = (
            "master-02-45d4a763" if role == "prefill" else "master"
        )
    original = next(x for x in data["dgd"]["spec"]["components"] if x["type"] == role)
    # A decode-local canary has no remote KV handoff. Preserve the explicit
    # local-prefill probe used by the PD baseline when copying aggregate flags.
    c["podTemplate"]["spec"]["containers"][0]["env"] += [
        copy.deepcopy(e)
        for e in original["podTemplate"]["spec"]["containers"][0].get("env", [])
        if e["name"] == "DYN_HEALTH_CHECK_PAYLOAD"
    ]
    oldargs = original["podTemplate"]["spec"]["containers"][0]["args"]
    kv = json.loads(oldargs[oldargs.index("--kv-transfer-config") + 1])
    if a.platform == "ascend":
        for side in ["prefill", "decode"]:
            kv["kv_connector_extra_config"][side]["tp_size"] = tp
    c["podTemplate"]["spec"]["containers"][0]["args"] += [
        "--disaggregation-mode",
        role,
        "--kv-transfer-config",
        json.dumps(kv),
    ]
    components.append(c)
data["dgd"]["spec"]["components"] = components
data["dgd"]["metadata"]["name"] = "m09-offline-" + a.model
data["dgdr"]["model"] = "/models/current"
data["dgdr"]["features"]["planner"]["max_gpu_budget"] = tp * 2
provenance = json.loads(
    (
        ROOT
        / f'adapter/m09/results/{a.platform}/provenance-{a.model if a.model=="qwen32" else "deepseek-final"}.json'
    ).read_text()
)
identity = data["identity"]
identity.update(
    model="/models/current",
    model_revision="config-sha256:" + provenance["model_config_sha256"],
    driver=provenance["driver"],
    parallelism={"prefill_tp": tp, "decode_tp": tp},
    topology={
        "nodes": 1 if a.model == "qwen32" or a.platform == "cuda" else 2,
        "measured_node": provenance["node"],
    },
)
identity["kv_config"] = {
    "worker_arguments": worker["podTemplate"]["spec"]["containers"][0]["args"]
}
if a.model == "deepseek":
    identity["precision"] = "fp8" if a.platform == "cuda" else "W8A8"
    data["dgdr"]["features"]["planner"]["itl_ms"] = (
        250 if a.platform == "cuda" else 1000
    )
    data["dgdr"]["features"]["planner"]["ttft_ms"] = 2000
    data["deployment_timeout"] = 2400
    if a.platform == "ascend":
        identity["driver"] = "prefill=24.1.rc3;decode=25.2.0"
        identity["topology"] = {
            "nodes": 2,
            "prefill_node": "master-02-45d4a763",
            "decode_node": "master",
        }
# The validated config identity is explicit, not a performance claim.
data["dgdr"]["features"]["planner"]["profile_identity"] = identity
data["tp"] = tp
data["pick"] = {"tp": tp}
if a.model == "deepseek":
    data["pick"].update(
        moe_tp=tp if a.platform == "cuda" else 1,
        moe_ep=1 if a.platform == "cuda" else tp,
    )
    identity["parallelism"].update(
        prefill_moe_tp=data["pick"]["moe_tp"],
        decode_moe_tp=data["pick"]["moe_tp"],
        prefill_moe_ep=data["pick"]["moe_ep"],
        decode_moe_ep=data["pick"]["moe_ep"],
    )
(a.output / "input.json").write_text(json.dumps(data, indent=2) + "\n")
tools["metadata"]["name"] = "m09-offline-" + a.model + "-tools"
pod = tools["spec"]
c = pod["containers"][0]
c["resources"]["requests"]["memory"] = "4Gi"
c["resources"]["limits"]["memory"] = "32Gi"
for v in pod["volumes"]:
    if v["name"] == "models":
        v["hostPath"]["path"] = next(
            v
            for v in frontend["podTemplate"]["spec"]["volumes"]
            if v["name"] == "models"
        )["hostPath"]["path"]
    elif v["name"] == "offline-work":
        v["hostPath"]["path"] = a.workdir
    elif v["name"] == "m09-source":
        v["hostPath"]["path"] = a.workdir + "/source"
    elif v["name"] == "m09-deps":
        v["hostPath"]["path"] = a.workdir + "/deps"
for m in c["volumeMounts"]:
    if m["name"] == "models":
        m["mountPath"] = "/models/current"
(a.output / "tools.json").write_text(json.dumps(tools, indent=2) + "\n")
