# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
from dynamo.common.platform.profile import (
    ProfileIdentity,
    validate_profile_manifest,
    write_profile_manifest,
)

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


def identity():
    return ProfileIdentity(
        platform="ascend",
        device_model="910B3",
        driver="25.2",
        sdk="9.1",
        engine="vllm",
        engine_version="0.23.0",
        plugin_version="0.23.0",
        model="Qwen3-0.6B",
        model_revision="sha256:index",
        precision="bf16",
        parallelism={"tp": 1, "dp": 1, "pp": 1},
        kv_config={"block_size": 128},
        topology={"nodes": 1, "devices_per_node": 8},
        runtime_image="sha256:runtime",
    )


def test_profile_roundtrip_tampering_and_expiry(tmp_path):
    source = tmp_path / "prefill_raw_data.json"
    source.write_text(json.dumps({"measured": 1}))
    write_profile_manifest(tmp_path, identity(), now=100)
    validate_profile_manifest(tmp_path, identity(), now=101)
    with pytest.raises(ValueError, match="expired"):
        validate_profile_manifest(tmp_path, identity(), now=1000, max_age_seconds=100)
    source.write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        validate_profile_manifest(tmp_path, identity(), now=101)


@pytest.mark.parametrize(
    "field,value",
    [
        ("platform", "cuda"),
        ("driver", "26.1"),
        ("engine_version", "0.28.0"),
        ("model_revision", "different"),
        ("parallelism", {"tp": 2}),
        ("kv_config", {"block_size": 16}),
        ("topology", {"nodes": 2}),
    ],
)
def test_cross_identity_reuse_rejected(tmp_path, field, value):
    (tmp_path / "prefill_raw_data.json").write_text("{}")
    write_profile_manifest(tmp_path, identity(), now=100)
    changed = identity().model_copy(update={field: value})
    with pytest.raises(ValueError, match="identity mismatch"):
        validate_profile_manifest(tmp_path, changed, now=101)


def test_added_bootstrap_file_requires_new_manifest(tmp_path):
    (tmp_path / "prefill_raw_data.json").write_text("{}")
    write_profile_manifest(tmp_path, identity(), now=100)
    (tmp_path / "decode_raw_data.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        validate_profile_manifest(tmp_path, identity(), now=101)
