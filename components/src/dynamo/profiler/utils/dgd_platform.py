# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Adapt generated vLLM defaults to an explicitly selected vendor platform.

Only generated defaults are translated. User overrides are applied afterwards
and remain subject to Operator admission and the engine's platform preflight.
"""

import copy
import json
import shlex

from dynamo.common.platform.kubernetes import DEVICE_RESOURCES
from dynamo.vllm.kv_connector_protocols import ASCEND_MOONCAKE_MODULE


def _main(component):
    containers = component.get("podTemplate", {}).get("spec", {}).get("containers", [])
    return next((c for c in containers if c.get("name") == "main"), {})


def _platform(container):
    values = [e for e in container.get("env", []) if e.get("name") == "DYN_PLATFORM"]
    if len(values) > 1:
        raise ValueError("DYN_PLATFORM must be declared once")
    return values[0].get("value") if values else None


def _args(container):
    args = container.get("args", [])
    return shlex.split(args) if isinstance(args, str) else list(args)


def _value(args, key, default=None):
    value = default
    for index, arg in enumerate(args):
        if arg == key:
            if index + 1 >= len(args):
                raise ValueError(f"Missing value for {key}")
            value = args[index + 1]
        elif arg.startswith(key + "="):
            value = arg.split("=", 1)[1]
    return value


def _without(args, key):
    result = []
    index = 0
    while index < len(args):
        if args[index] == key:
            index += 2
        elif args[index].startswith(key + "="):
            index += 1
        else:
            result.append(args[index])
            index += 1
    return result


def adapt_vllm_blueprint_platform(blueprint, override):
    """Translate default resources/transport before merging v1beta1 overrides.

    TP counts come from the generated candidate, not a fixed one-card resource
    override. CPU Frontends, CUDA, and configurations without an explicit
    vendor selection retain their original defaults.
    """
    if not override or override.get("apiVersion") != "nvidia.com/v1beta1":
        return blueprint
    selected = {
        c["name"]: _platform(_main(c))
        for c in override.get("spec", {}).get("components", [])
    }
    result = copy.deepcopy(blueprint)
    workers = [
        c
        for c in result.get("spec", {}).get("components", [])
        if c.get("type") in {"worker", "prefill", "decode"}
    ]
    roles = {}
    for component in workers:
        args = _args(_main(component))
        role = _value(args, "--disaggregation-mode")
        if role in {"prefill", "decode"}:
            roles[role] = {
                "tp_size": int(_value(args, "--tensor-parallel-size", "1")),
                "dp_size": int(_value(args, "--data-parallel-size", "1")),
                "pp_size": int(_value(args, "--pipeline-parallel-size", "1")),
            }
    for component in workers:
        platform = selected.get(component["name"])
        if platform not in {"ascend", "metax"}:
            continue
        resource_key = DEVICE_RESOURCES[platform]
        container = _main(component)
        for field in ("requests", "limits"):
            resources = container.get("resources", {}).get(field, {})
            if "nvidia.com/gpu" in resources:
                if resource_key in resources:
                    raise ValueError(
                        f"Generated blueprint mixes CUDA and {platform} resources"
                    )
                resources[resource_key] = resources.pop("nvidia.com/gpu")
        args = _args(container)
        raw = _value(args, "--kv-transfer-config")
        default = {"kv_connector": "NixlConnector", "kv_role": "kv_both"}
        if raw is not None and json.loads(raw) != default:
            continue  # Preserve a custom connector; do not silently rewrite it.
        role = _value(args, "--disaggregation-mode")
        args = _without(args, "--kv-transfer-config")
        if role in {"prefill", "decode"}:
            if set(roles) != {"prefill", "decode"}:
                raise ValueError(
                    "Vendor PD generation requires both peer parallel configurations"
                )
            extra = copy.deepcopy(roles)
            if platform == "ascend" and (
                roles["prefill"]["tp_size"],
                roles["decode"]["tp_size"],
            ) == (1, 2):
                extra["heyi_tp_split"] = True
            connector = {
                "kv_connector": "MooncakeConnector",
                "kv_role": "kv_producer" if role == "prefill" else "kv_consumer",
                "kv_connector_extra_config": extra,
            }
            if platform == "ascend":
                connector["kv_connector_module_path"] = ASCEND_MOONCAKE_MODULE
            else:
                extra["mooncake_protocol"] = "maca"
            args += ["--kv-transfer-config", json.dumps(connector)]
        container["args"] = args
    return result
