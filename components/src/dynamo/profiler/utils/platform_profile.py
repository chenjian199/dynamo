# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prevent the external candidate enumerator's implicit H200 capacity fallback."""

import hashlib
import json
import math
import os
import time
from pathlib import Path

import yaml
from dynamo.common.platform.kubernetes import container_platform
from dynamo.common.platform.profile import ProfileIdentity


def dgdr_platform(dgdr):
    """Infer the target from explicit worker overrides, never from SKU aliases."""
    overrides = getattr(dgdr, "overrides", None)
    override = overrides.dgd if overrides else None
    platforms = set()
    for component in (override or {}).get("spec", {}).get("components", []):
        if component.get("type") not in {"worker", "prefill", "decode"}:
            continue
        for container in (
            component.get("podTemplate", {}).get("spec", {}).get("containers", [])
        ):
            if container.get("name") == "main":
                platforms.add(container_platform(container))
    if len(platforms) > 1 or "cpu" in platforms:
        raise ValueError("Profiler workers must target one accelerator platform")
    return next(iter(platforms), "cuda")


def record_candidate_profile(directory, candidate):
    """Bind actual candidate configuration and AIPerf artifacts to its identity.

    Driver/SDK/model revision are operator-supplied provenance, not attestation.
    Aggregate AIPerf latency is not a forward-pass sample and must never be
    consumed by the Planner's legacy FPM bootstrap loader.
    """
    identity_path = os.environ.get("DYN_PROFILE_IDENTITY")
    if not identity_path:
        return
    base = ProfileIdentity.model_validate_json(Path(identity_path).read_text())
    parallelism = {
        name: int(getattr(candidate, name))
        for name in ("tp", "pp", "dp", "moe_tp", "moe_ep")
        if getattr(candidate, name, None) is not None
    }
    identity = base.model_copy(update={"parallelism": parallelism})
    directory = Path(directory)
    paths = sorted(directory.rglob("profile_export_aiperf.json"))
    if not paths:
        return
    paths.append(directory / "config.yaml")
    files = {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    manifest = {
        "schema_version": 1,
        "kind": "benchmark-results",
        "identity_source": "operator environment; parallelism from enumerated candidate",
        "identity": identity.model_dump(),
        "fingerprint": identity.fingerprint(),
        "created_at": time.time(),
        "files": files,
        "planner_bootstrap_compatible": False,
    }
    (directory / "profile-identity.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )


def require_system_capacity(system, systems_paths, vram_mb=None):
    """Require a real capacity definition; this does not certify performance data.

    AISimulate 0.12.0's enumerator defaults unknown systems to H200/141 GiB.
    The first matching YAML is authoritative, matching its lookup order.
    """
    if not system or Path(system).name != system:
        raise ValueError("Invalid hardware system identifier")
    for root in systems_paths:
        path = Path(root) / f"{system}.yaml"
        if not path.is_file():
            continue
        spec = yaml.safe_load(path.read_text())
        capacity = spec.get("gpu", {}).get("mem_capacity")
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, (int, float))
            or not math.isfinite(capacity)
            or capacity <= 0
        ):
            raise ValueError(f"{path}: missing positive device memory capacity")
        if vram_mb is not None and (
            not math.isfinite(vram_mb) or vram_mb <= 0 or capacity > vram_mb * 2**20
        ):
            raise ValueError(
                f"{system}: enumerator memory exceeds declared device capacity; correct its capacity YAML before profiling"
            )
        return {
            "system": system,
            "capacity_bytes": capacity,
            "source": str(path),
            "performance_database_certified": False,
        }
    raise ValueError(
        f"No capacity definition for {system}; refusing implicit H200 memory fallback. Supply measured device capacity in the AISimulate system YAML; use thorough measurements for performance."
    )
