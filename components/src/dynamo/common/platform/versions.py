# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pinned engine baselines for the three hardware certification environments."""

import importlib.metadata

from dynamo.common.platform.registry import PlatformError

VLLM_VERSIONS = {"cuda": "0.28.0", "metax": "0.26.0", "ascend": "0.23.0"}


def validate_vllm_versions(platform_id):
    expected = VLLM_VERSIONS.get(platform_id)
    if expected is None:
        return {}
    packages = ["vllm"]
    if platform_id != "cuda":
        packages.append(f"vllm-{platform_id}")
    versions = {}
    for package in packages:
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise PlatformError(
                "unsupported_stack",
                platform_id,
                f"required package {package} is missing",
            ) from error
        if actual.split("+")[0] != expected:
            raise PlatformError(
                "unsupported_stack",
                platform_id,
                f"pinned {package} version is {expected}; installed version is {actual}",
            )
        versions[package] = actual
    return versions
