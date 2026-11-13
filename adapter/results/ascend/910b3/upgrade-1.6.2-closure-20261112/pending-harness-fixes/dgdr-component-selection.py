# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

def inference_components(dgd):
    """Resolve generated component identities from their serving configuration."""
    spec = dgd["spec"]
    components = spec.get("components")
    if components is None:
        components = [dict(service, name=name) for name, service in spec["services"].items()]
    roles = {"decode": [], "prefill": []}
    for component in components:
        kind = component.get("type", component.get("componentType", ""))
        if kind in ("frontend", "Frontend", "planner", "Planner"):
            continue
        containers = component.get("podTemplate", {}).get("spec", {}).get("containers", [])
        legacy_main = component.get("extraPodSpec", {}).get("mainContainer")
        if legacy_main is not None:
            containers = [dict(legacy_main, name="main"), *containers]
        args = component.get("args", [])
        for container in containers:
            if container.get("name") == "main":
                args = container.get("args", [])
                break
        mode = None
        for index, token in enumerate(args):
            if token == "--disaggregation-mode":
                mode = args[index + 1]
            elif token.startswith("--disaggregation-mode="):
                mode = token.split("=", 1)[1]
            elif token == "--is-prefill-worker":
                mode = "prefill"
        sub = component.get("subComponentType", "").lower()
        if mode is None and sub in ("prefill", "decode"):
            mode = sub
        if mode is None and kind.lower() == "worker":
            mode = "decode"  # Aggregated serving also accepts decode requests.
        if mode in roles:
            roles[mode].append(component["name"])
    if len(roles["decode"]) != 1 or len(roles["prefill"]) > 1:
        raise ValueError(f"Expected one decode component and at most one prefill: {roles}")
    status = dgd["status"]["components"]
    decode = status[roles["decode"][0]]
    prefill = status[roles["prefill"][0]] if roles["prefill"] else None
    if decode["replicas"] < 1 or not decode["runtimeNamespace"]:
        raise ValueError("Decode component has no active runtime")
    if prefill is not None and (
        prefill["replicas"] < 1 or prefill["runtimeNamespace"] != decode["runtimeNamespace"]
    ):
        raise ValueError("Prefill component is absent or in a different runtime namespace")
    return decode, prefill
