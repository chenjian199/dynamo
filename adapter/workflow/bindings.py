# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve one frozen case's actual nodes, assets, topology and API prerequisites."""

import re
from copy import deepcopy


def case_environment(environment, feature, case):
    binding = environment.get("case_bindings", {}).get(case["id"])
    if not isinstance(binding, dict):
        raise TypeError("Missing environment binding for " + case["id"])
    if not binding.get("nodes") or len(set(binding["nodes"])) != len(binding["nodes"]):
        raise ValueError("Case needs distinct declared node IDs")
    nodes = {n["id"]: n for n in environment["nodes"]}
    if set(binding["nodes"]) - set(nodes):
        raise ValueError("Case references unknown node")
    env = deepcopy(environment)
    env["nodes"] = [deepcopy(nodes[k]) for k in binding["nodes"]]
    image = binding.get("image")
    if "images" in feature["requirements"]:
        if not isinstance(image, str) or not re.search(r"@sha256:[a-f0-9]{64}$", image):
            raise ValueError("Case execution image must be digest-pinned")
        for node in env["nodes"]:
            if image not in node.get("images", []):
                raise ValueError("Case image absent from node inventory declaration")
            node["images"] = [image]
    model_ids = binding.get("models", [])
    if "models" in feature["requirements"] and not model_ids:
        raise ValueError("Case requires immutable model IDs")
    for node in env["nodes"]:
        models = {m["id"]: m for m in node.get("models", []) if isinstance(m, dict)}
        if set(model_ids) - set(models):
            raise ValueError("Case model absent on node " + node["id"])
        node["models"] = [models[k] for k in model_ids]
    topology = binding.get("topology", {})
    for name, width in topology.items():
        if name in {"tp", "dp", "pp", "ep", "prefill_replicas", "decode_replicas"} and (
            type(width) is not int or width < 1
        ):
            raise ValueError(
                "Parallel widths must be positive; native engine combinations are not whitelisted"
            )
    if "kubernetes" in feature["requirements"]:
        k = env.get("kubernetes", {})
        selected = binding.get("kubernetes_nodes")
        if not selected or not set(selected).issubset(k.get("nodes", [])):
            raise ValueError("Case requires declared Kubernetes nodes")
        k["nodes"] = selected
        k["required_apis"] = list(
            dict.fromkeys(k.get("required_apis", []) + case.get("required_apis", []))
        )
        k["required_resources"] = list(
            dict.fromkeys(
                k.get("required_resources", []) + binding.get("required_resources", [])
            )
        )
    return env, deepcopy(binding)
