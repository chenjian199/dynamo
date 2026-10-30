# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Whole-device resources shared by Planner and Profiler (M08 contract).

No SDK is imported on the CPU control plane. DRA/MIG and auxiliary allocations
must use current Operator status rather than this main-container scalar count.
"""

import re

DEVICE_RESOURCES = {
    "cuda": "nvidia.com/gpu",
    "metax": "metax-tech.com/gpu",
    "ascend": "huawei.com/Ascend910",
}


def whole_device_count(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"Invalid GPU count {value!r}; expected a positive integer")
    count = int(value)
    if count <= 0:
        raise ValueError(f"Invalid GPU count {value!r}; expected a positive integer")
    return count


def container_platform(container):
    """Resolve literal identity, preserving the implicit CUDA legacy default."""
    env = [e for e in container.get("env", []) if e.get("name") == "DYN_PLATFORM"]
    if len(env) > 1 or (env and ("valueFrom" in env[0] or not env[0].get("value"))):
        raise ValueError("DYN_PLATFORM must be one nonempty literal value")
    declared = env[0]["value"] if env else None
    if declared is not None and declared not in {*DEVICE_RESOURCES, "cpu"}:
        raise ValueError(f"Unknown DYN_PLATFORM {declared!r}")
    resources = container.get("resources") or {}
    names = set(resources.get("limits", {})) | set(resources.get("requests", {}))
    owners = set()
    for name in names:
        for platform, key in DEVICE_RESOURCES.items():
            if name == key or (
                platform == "cuda" and name.startswith("nvidia.com/mig-")
            ):
                owners.add(platform)
        if (
            name.startswith(("huawei.com/", "metax-tech.com/"))
            and name not in DEVICE_RESOURCES.values()
        ):
            raise ValueError(f"Unsupported allocation unit {name!r}")
    if len(owners) > 1:
        raise ValueError("Container mixes accelerator platforms")
    owner = next(iter(owners), None)
    if owner in {"metax", "ascend"} and declared != owner:
        raise ValueError(f"{owner} resources require matching literal DYN_PLATFORM")
    if owner and declared and declared != owner:
        raise ValueError(f"DYN_PLATFORM {declared!r} conflicts with {owner} resources")
    return declared or owner or "cuda"


def scalar_device_count(container):
    platform = container_platform(container)
    if platform not in DEVICE_RESOURCES:
        raise ValueError(f"No GPU count specified for {platform} container")
    key = DEVICE_RESOURCES[platform]
    resources = container.get("resources") or {}
    limits, requests = resources.get("limits", {}), resources.get("requests", {})
    if key not in limits and key not in requests:
        raise ValueError(f"No GPU count specified; set resources.limits.{key}")
    if platform == "cuda":
        # Preserve the existing CUDA limits-first read policy. Kubernetes
        # admission remains responsible for validating the actual Pod request.
        return whole_device_count(limits[key] if key in limits else requests[key])
    counts = [
        whole_device_count(values[key])
        for values in (limits, requests)
        if key in values
    ]
    if len(set(counts)) != 1:
        raise ValueError(f"{key} requests and limits must match")
    return counts[0]
