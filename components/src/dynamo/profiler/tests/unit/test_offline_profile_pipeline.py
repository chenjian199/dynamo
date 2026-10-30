# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the standard Profiler entry point's persisted-profile handoff."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest
from dynamo.common.platform.profile import ProfileIdentity, validate_profile_manifest
from dynamo.planner.config.parallelization import PickedParallelConfig
from dynamo.profiler import profile_sla
from dynamo.profiler.utils.dgd_generation import add_profile_data_to_config
from dynamo.profiler.utils.dgdr_v1beta1_types import DynamoGraphDeploymentRequestSpec
from dynamo.profiler.utils.profile_common import ProfilerOperationalConfig

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.delenv("DYN_PROFILE_IDENTITY", raising=False)
    monkeypatch.setenv("DGDR_NAME", "test-offline")
    identity = ProfileIdentity(
        platform="cuda",
        device_model="H800",
        driver="580",
        sdk="13",
        engine="vllm",
        engine_version="0.28.0",
        plugin_version="builtin",
        model="Qwen/Qwen3-0.6B",
        model_revision="config-sha256",
        precision="bf16",
        parallelism={"tp": 8},
        kv_config={"block_size": 128},
        topology={"nodes": 1},
        runtime_image="sha256:runtime",
    )
    dgdr = DynamoGraphDeploymentRequestSpec.model_validate(
        {
            "model": identity.model,
            "backend": "vllm",
            "image": "locked:runtime",
            "hardware": {"gpuSku": "h800_sxm", "totalGpus": 8, "numGpusPerNode": 8},
            "workload": {"isl": 512, "osl": 128},
            "sla": {"ttft": 1000, "itl": 100},
            "features": {
                "planner": {
                    "mode": "disagg",
                    "optimization_target": "sla",
                    "enable_throughput_scaling": True,
                    "enable_load_scaling": False,
                    "pre_deployment_sweeping_mode": "rapid",
                    "pre_deployment_metrics_source": "files",
                    "profile_identity": identity,
                }
            },
        }
    )
    base = {"metadata": {"name": "test-offline"}, "spec": {"components": []}}
    selected = {"chosen_exp": "disagg", "resolved_backend": "vllm", "dgd_config": base}
    strategy = AsyncMock(
        return_value=(
            selected,
            PickedParallelConfig(tp=1),
            PickedParallelConfig(tp=2),
            1000,
            100,
        )
    )
    monkeypatch.setattr(profile_sla, "_execute_strategy", strategy)
    monkeypatch.setattr(profile_sla, "_check_dgdr_aic_support", lambda *a: False)
    monkeypatch.setattr(profile_sla, "validate_dgdr_dynamo_features", lambda *a: None)
    monkeypatch.setattr(
        profile_sla,
        "get_model_config_from_model_path",
        lambda *a: {"max_position_embeddings": 4096},
    )
    monkeypatch.setattr(profile_sla, "materialize_dgd", lambda value, **kw: value)
    monkeypatch.setattr(profile_sla, "cleanup_remaining_deployments", AsyncMock())

    async def measure(_dgdr, ops, *args, **kwargs):
        output = Path(ops.output_dir)
        for role in ("prefill", "decode"):
            (output / f"selected_{role}_interpolation").mkdir()
        np.savez(
            output / "selected_prefill_interpolation/raw_data.npz",
            prefill_isl=[100],
            prefill_ttft=[10.0],
            prefill_thpt_per_gpu=[10000.0],
        )
        np.savez(
            output / "selected_decode_interpolation/raw_data.npz",
            x_kv_usage=[0.1],
            y_context_length=[100.0],
            z_itl=[10.0],
            z_thpt_per_gpu=[100.0],
            max_kv_tokens=[1000],
        )

    interpolation = AsyncMock(side_effect=measure)
    monkeypatch.setattr(profile_sla, "run_interpolation", interpolation)
    packaged = []

    def assemble(request, ops, *args, **kwargs):
        assert kwargs["aic_spec"] is None
        assert kwargs["aic_perf_model"] is None
        # Fail here if run_profile forgot the manifest or wrote it too late.
        validate_profile_manifest(
            ops.output_dir, request.features.planner.profile_identity
        )
        cm = add_profile_data_to_config(base, ops.output_dir)
        packaged.append(cm)
        return [cm]

    monkeypatch.setattr(profile_sla, "assemble_final_config", assemble)
    return dgdr, identity, selected, strategy, interpolation, packaged


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["dgdr", "environment", "both"])
async def test_profile_entrypoint_packages_identity_before_assembly(
    pipeline, tmp_path, monkeypatch, source
):
    dgdr, identity, _, _, interpolation, packaged = pipeline
    if source != "dgdr":
        path = tmp_path / "input-identity.json"
        path.write_text(identity.model_dump_json())
        monkeypatch.setenv("DYN_PROFILE_IDENTITY", str(path))
    if source == "environment":
        dgdr.features.planner.profile_identity = None
    await profile_sla.run_profile(
        dgdr, ProfilerOperationalConfig(output_dir=str(tmp_path))
    )
    interpolation.assert_awaited_once()
    manifest = json.loads(packaged[0]["data"]["profile-identity.json"])
    assert manifest["identity"]["parallelism"]["prefill_tp"] == 1
    assert manifest["identity"]["parallelism"]["decode_tp"] == 2
    assert set(manifest["files"]) == {"prefill_raw_data.json", "decode_raw_data.json"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["conflict", "missing", "aggregated", "attention_dp"]
)
async def test_file_only_pipeline_rejects_unusable_handoff(
    pipeline, tmp_path, monkeypatch, invalid
):
    dgdr, identity, selected, strategy, interpolation, packaged = pipeline
    if invalid == "conflict":
        path = tmp_path / "input-identity.json"
        path.write_text(
            identity.model_copy(update={"driver": "different"}).model_dump_json()
        )
        monkeypatch.setenv("DYN_PROFILE_IDENTITY", str(path))
        match = "identities conflict"
    elif invalid == "missing":
        dgdr.features.planner.profile_identity = None
        match = "explicit profile identity"
    elif invalid == "aggregated":
        selected["chosen_exp"] = "agg"
        match = "disaggregated pick"
    else:
        pick_result = list(strategy.return_value)
        pick_result[2] = PickedParallelConfig(tp=2, dp=2)
        strategy.return_value = tuple(pick_result)
        match = "rank groups"
    with pytest.raises(ValueError, match=match):
        await profile_sla.run_profile(
            dgdr, ProfilerOperationalConfig(output_dir=str(tmp_path))
        )
    interpolation.assert_not_awaited()
    assert not packaged
    if invalid in {"conflict", "missing"}:
        strategy.assert_not_awaited()
