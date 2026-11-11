#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reject editable/source-shadowed imports and record the installed binaries."""
import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path


def validate_versions(profile, platform, version=metadata.version):
    """Use the frozen platform lock, including plugin and torch variants."""
    assert profile["id"] == platform, (profile["id"], platform)
    assert "vllm" in profile["versions"], "Platform lock has no vLLM version"
    for package, expected in profile["versions"].items():
        actual = version(package)
        assert actual == expected, (package, actual, expected)


def main():
    platform = os.environ["DYNAMO_IMAGE_PLATFORM"]
    profile_path = Path(
        os.environ.get("DYNAMO_EXPECTED_PROFILE", "/opt/dynamo/artifacts/profile.json")
    )
    profile = json.loads(profile_path.read_text())
    validate_versions(profile, platform)
    source = json.loads(Path("/opt/dynamo/artifacts/SOURCE.json").read_text())
    packages = {}
    packaging = {}
    for name in ("ai-dynamo", "ai-dynamo-runtime", "kvbm", "vllm", "torch"):
        distribution = metadata.distribution(name)
        direct = distribution.read_text("direct_url.json")
        packaging[name] = json.loads(direct) if direct else None
        # Vendor engines may be installed editable against sources embedded in the
        # immutable official base. Only Dynamo-owned distributions must be wheels.
        if name in {"ai-dynamo", "ai-dynamo-runtime", "kvbm"}:
            assert not direct or not json.loads(direct).get("dir_info", {}).get(
                "editable"
            ), name
        packages[name] = distribution.version
    modules = {}
    for name in (
        "dynamo._core",
        "dynamo.vllm",
        "dynamo.common.platform",
        "dynamo.planner",
        "dynamo.profiler",
        "kvbm._core",
        "gpu_memory_service",
    ):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        assert "site-packages" in path.parts, (name, str(path))
        modules[name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    print(
        json.dumps(
            {
                "passed": True,
                "platform": platform,
                "source_commit": source["commit"],
                "source_patch_sha256": source.get("patch_sha256"),
                "packages": packages,
                "packaging": packaging,
                "modules": modules,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
