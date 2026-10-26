# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest

from dynamo.common.platform import versions
from dynamo.common.platform.registry import PlatformError

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


@pytest.mark.parametrize("platform", ["cuda", "metax", "ascend"])
def test_pinned_engine_and_plugin_with_vendor_build_suffix(monkeypatch, platform):
    expected = versions.VLLM_VERSIONS[platform]
    monkeypatch.setattr(
        versions.importlib.metadata, "version", lambda package: expected + "+build"
    )
    actual = versions.validate_vllm_versions(platform)
    assert actual["vllm"] == expected + "+build"
    assert len(actual) == (1 if platform == "cuda" else 2)


@pytest.mark.parametrize(
    "platform,package,wrong",
    [
        ("cuda", "vllm", "0.24.0"),
        ("cuda", "vllm", "0.29.0"),
        ("metax", "vllm-metax", "0.25.0"),
        ("ascend", "vllm", "0.24.0"),
    ],
)
def test_version_drift_is_rejected(monkeypatch, platform, package, wrong):
    monkeypatch.setattr(
        versions.importlib.metadata,
        "version",
        lambda name: wrong if name == package else versions.VLLM_VERSIONS[platform],
    )
    with pytest.raises(PlatformError, match="pinned"):
        versions.validate_vllm_versions(platform)
