#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render Qwen32B TP2 PD or reversed small-model PD with disjoint host ports."""

import argparse
import json

from render import NODES, pd_serving

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=NODES, required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
p.add_argument("--prefill", type=int, default=1)
p.add_argument("--decode", type=int, default=1)
p.add_argument("--reverse-small", action="store_true")
p.add_argument("--small-tp", choices=["2:1", "1:2"])
args = p.parse_args()
cuda = args.platform == "cuda"
paths = (
    ["/ssd/models/dynamo-regression/Qwen3-32B", "/root/Qwen3-32B"]
    if cuda
    else ["/data/Qwen3-32B"] * 2
)
model = "Qwen3-32B"
tp = 2
prefix = "large-pd"
offset = 3000
if args.reverse_small or args.small_tp:
    model = "Qwen3-0.6B"
    tp = 1
    prefix = "reverse-pd"
    offset = 6000
    paths = (
        ["/ssd/models/dynamo-regression/Qwen3-0.6B"] * 2
        if cuda
        else ["/data/weight/Qwen3-0.6B", "/data/dynamo-regression-models/Qwen3-0.6B"]
    )
if args.small_tp:
    if args.reverse_small:
        p.error("--small-tp and --reverse-small are separate campaigns")
    # These split cases run sequentially. DYN_SYSTEM_PORT is signed i16.
    prefix = "split-pd-" + args.small_tp.replace(":", "-")
    offset = 11000
ptp, dtp = map(int, args.small_tp.split(":")) if args.small_tp else (tp, tp)
models = dict(zip(NODES[args.platform], paths))
if args.reverse_small:
    models = dict(reversed(list(models.items())))
obj = pd_serving(args.platform, args.namespace, models, args.prefill, args.decode)
ips = dict(
    zip(
        NODES[args.platform],
        ["192.168.4.6", "192.168.4.14"] if cuda else ["192.168.7.7", "192.168.7.5"],
    )
)
# Keep fixed listeners below Linux's usual ephemeral range and Kubernetes NodePorts.
# System, metrics and event ports use separate ranges; split cases remain sequential.
transfer_base = 17500 if args.small_tp else (16500 if args.reverse_small else 15500)
event_base = 28080 if args.small_tp else 20080 + offset
index = 0
for item in obj["items"]:
    old = item["metadata"]["name"]
    if not old.startswith("pd-"):
        continue
    name = prefix + old[2:]
    item["metadata"]["name"] = name
    if item["kind"] == "Service":
        item["spec"]["selector"]["app"] = name
        continue
    item["spec"]["selector"]["matchLabels"]["app"] = name
    item["spec"]["template"]["metadata"]["labels"]["app"] = name
    spec = item["spec"]["template"]["spec"]
    c = spec["containers"][0]
    env = {x["name"]: x["value"] for x in c["env"]}
    env["DYN_NAMESPACE"] = prefix
    if old == "pd-frontend":
        c["env"] = [{"name": k, "value": v} for k, v in env.items()]
        c["resources"]["requests"].update(cpu="500m", memory="1Gi")
        c["resources"]["limits"].update(cpu="4", memory="4Gi")
        continue
    tp = ptp if old.startswith("pd-prefill") else dtp
    node = spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchFields"][0]["values"][0]
    env.update(
        VLLM_HOST_IP=ips[node],
        DYN_SYSTEM_PORT=str(18100 + offset + index * 16),
        DYN_FORWARDPASS_METRIC_PORT=str(18200 + offset + index * 16),
        VLLM_NIXL_SIDE_CHANNEL_PORT=str(transfer_base + index * 16),
        VLLM_MOONCAKE_BOOTSTRAP_PORT=str(transfer_base + 100 + index * 16),
    )
    cmd = c["command"]
    cmd[cmd.index("--model") + 1] = "/models/" + model
    cmd[cmd.index("--served-model-name") + 1] = prefix + "-model"
    cmd += ["--tensor-parallel-size", str(tp)]
    event = json.loads(cmd[cmd.index("--kv-events-config") + 1])
    event["endpoint"] = f"tcp://*:{event_base+index*16}"
    cmd[cmd.index("--kv-events-config") + 1] = json.dumps(event)
    kv = json.loads(cmd[cmd.index("--kv-transfer-config") + 1])
    if not cuda:
        kv["kv_port"] = transfer_base + 100 + index * 16
        for role, value in [("prefill", ptp), ("decode", dtp)]:
            kv["kv_connector_extra_config"][role]["tp_size"] = value
        kv["kv_connector_extra_config"]["heyi_tp_split"] = ptp < dtp
    cmd[cmd.index("--kv-transfer-config") + 1] = json.dumps(kv)
    c["env"] = [{"name": k, "value": v} for k, v in env.items()]
    for m in c["volumeMounts"]:
        if m["name"] == "model":
            m["mountPath"] = "/models/" + model
    resource = "nvidia.com/gpu" if cuda else "huawei.com/Ascend910"
    for key in ("requests", "limits"):
        c["resources"][key][resource] = tp
    if tp == 2 and model == "Qwen3-32B":
        c["resources"]["requests"].update(cpu="8", memory="16Gi")
        c["resources"]["limits"].update(cpu="16", memory="96Gi")
    index += 1
print(json.dumps(obj, indent=2))
