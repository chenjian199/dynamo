# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Persisted profiles must survive transport and never silently fall back."""
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from dynamo.common.platform.profile import (
    ProfileIdentity,
    validate_profile_manifest,
    write_profile_manifest,
)
from dynamo.planner.config.defaults import SubComponentType
from dynamo.planner.monitoring import perf_metrics
from dynamo.profiler.utils.dgd_generation import add_profile_data_to_config

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


@pytest.fixture
def profile(tmp_path):
    identity = ProfileIdentity(
        platform="ascend",
        device_model="910B3",
        driver="25.2",
        sdk="9.1",
        engine="vllm",
        engine_version="0.23.0",
        plugin_version="0.23.0",
        model="Qwen3-0.6B",
        model_revision="sha256:config",
        precision="bf16",
        parallelism={"prefill_tp": 1, "decode_tp": 1},
        kv_config={"bytes": 536870912},
        topology={"nodes": 1},
        runtime_image="sha256:runtime",
    )
    for phase in ("prefill", "decode"):
        (tmp_path / f"selected_{phase}_interpolation").mkdir()
    np.savez(
        tmp_path / "selected_prefill_interpolation/raw_data.npz",
        prefill_isl=[100, 300, 500],
        prefill_ttft=[10.0, 20.0, 30.0],
        prefill_thpt_per_gpu=[10000.0, 15000.0, 16666.0],
    )
    np.savez(
        tmp_path / "selected_decode_interpolation/raw_data.npz",
        x_kv_usage=[0.1, 0.2],
        y_context_length=[100.0, 200.0],
        z_itl=[10.0, 20.0],
        z_thpt_per_gpu=[1000.0, 1000.0],
        max_kv_tokens=[1000],
    )
    write_profile_manifest(tmp_path, identity)
    return tmp_path, identity


@pytest.mark.asyncio
async def test_json_configmap_roundtrip_and_file_only_bootstrap(
    profile, tmp_path, monkeypatch
):
    directory, identity = profile
    configmap = add_profile_data_to_config({"spec": {"components": []}}, str(directory))
    mounted = tmp_path / "mounted"
    mounted.mkdir()
    for name, content in configmap["data"].items():
        (mounted / name).write_text(content)
    manifest = validate_profile_manifest(mounted, identity)
    assert set(manifest["files"]) == {"prefill_raw_data.json", "decode_raw_data.json"}
    endpoint = AsyncMock(side_effect=AssertionError("Must not use live samples"))
    monkeypatch.setattr(perf_metrics, "_try_endpoint", endpoint)
    for component, expected in [
        (SubComponentType.PREFILL, 3),
        (SubComponentType.DECODE, 2),
    ]:
        for _restart in range(2):
            fpms = await perf_metrics.fetch_pre_deployment_metrics(
                MagicMock(),
                "test",
                MagicMock(),
                str(mounted),
                component,
                target_platform="ascend",
                profile_identity=identity,
                source="files",
            )
            assert len(fpms) == expected
    endpoint.assert_not_awaited()
    (mounted / "decode_raw_data.json").write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        await perf_metrics.fetch_pre_deployment_metrics(
            MagicMock(),
            "test",
            MagicMock(),
            str(mounted),
            SubComponentType.DECODE,
            target_platform="ascend",
            profile_identity=identity,
            source="files",
        )


@pytest.mark.asyncio
async def test_file_only_rejects_wrong_identity_and_missing_data(profile):
    directory, identity = profile
    for expected in (identity.model_copy(update={"engine_version": "0.28.0"}), None):
        with pytest.raises(ValueError):
            await perf_metrics.fetch_pre_deployment_metrics(
                MagicMock(),
                "test",
                MagicMock(),
                str(directory),
                SubComponentType.PREFILL,
                profile_identity=expected,
                source="files",
            )
    (directory / "selected_prefill_interpolation/raw_data.npz").unlink()
    with pytest.raises(ValueError, match="inventory"):
        await perf_metrics.fetch_pre_deployment_metrics(
            MagicMock(),
            "test",
            MagicMock(),
            str(directory),
            SubComponentType.PREFILL,
            profile_identity=identity,
            source="files",
        )


@pytest.mark.asyncio
async def test_hashed_but_invalid_measurement_is_rejected(profile):
    directory, identity = profile
    np.savez(
        directory / "selected_prefill_interpolation/raw_data.npz",
        prefill_isl=[100, 300, 500],
        prefill_ttft=[10.0, float("nan"), 30.0],
    )
    write_profile_manifest(directory, identity)
    with pytest.raises(ValueError, match="positive finite"):
        await perf_metrics.fetch_pre_deployment_metrics(
            None,
            "unused",
            None,
            str(directory),
            SubComponentType.PREFILL,
            profile_identity=identity,
            source="files",
        )


@pytest.mark.asyncio
async def test_file_bootstrap_rejects_ungrouped_attention_dp(profile):
    directory, identity = profile
    identity = identity.model_copy(
        update={"parallelism": {"prefill_tp": 1, "decode_tp": 1, "decode_dp": 2}}
    )
    write_profile_manifest(directory, identity)
    with pytest.raises(ValueError, match="rank groups"):
        await perf_metrics.fetch_pre_deployment_metrics(
            None,
            "unused",
            None,
            str(directory),
            SubComponentType.DECODE,
            profile_identity=identity,
            source="files",
        )
