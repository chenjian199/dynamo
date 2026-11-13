# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Environment declaration checks, independent of execution/state imports."""

import re


def valid_hardware_uuid(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value)
        is not None
        and value.replace("-", "") not in ("0" * 32, "f" * 32)
    )


def validate_environment(env):
    if not isinstance(env.get("nodes"), list) or not env["nodes"]:
        raise ValueError("Environment must list nodes")
    ids = []
    for node in env["nodes"]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", node.get("id", "")):
            raise ValueError("Invalid node ID")
        ids.append(node["id"])
        host = node.get("ssh")
        if host and (
            host.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_@.:-]+", host)
        ):
            raise ValueError("Use an SSH alias; no passwords or shell expressions")
        if not node.get("workspace"):
            raise ValueError("Node workspace is required")
        if host and not node["workspace"].startswith("/"):
            raise ValueError("Remote node workspace must be an absolute path")
        if node.get("container_workspace") and not node[
            "container_workspace"
        ].startswith("/"):
            raise ValueError("Container workspace must be an absolute path")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate node IDs")
    targets = [node.get("ssh") or "localhost" for node in env["nodes"]]
    if len(targets) != len(set(targets)):
        raise ValueError("Duplicate SSH targets; describe a physical node only once")
    if "asset_probe_timeout_seconds" in env and (
        type(env["asset_probe_timeout_seconds"]) is not int
        or env["asset_probe_timeout_seconds"] <= 0
    ):
        raise ValueError("Positive asset_probe_timeout_seconds required")
    for node in env["nodes"]:
        identity = node.get("host_identity")
        if identity is not None and (
            not isinstance(identity, dict)
            or set(identity) != {"kind", "value"}
            or identity.get("kind") != "dmi_product_uuid"
            or not valid_hardware_uuid(identity.get("value"))
        ):
            raise ValueError("host_identity requires a pinned non-placeholder DMI UUID")
        if node.get("machine_id") and not re.fullmatch(
            r"[0-9a-f]{32}", node["machine_id"]
        ):
            raise ValueError("machine_id must be an observed /etc/machine-id")
    k = env.get("kubernetes", {})
    for api in k.get("required_apis", []):
        if not re.fullmatch(r"[a-z0-9.-]+/v[0-9]+(?:(?:alpha|beta)[0-9]+)?", api):
            raise ValueError("required_apis must contain API group/version pairs")
    budget = env.get("budgets", {})
    for field in ["task_timeout_seconds", "max_attempts", "max_tasks"]:
        if type(budget.get(field)) is not int or budget[field] <= 0:
            raise ValueError(f"Positive budget required: {field}")
