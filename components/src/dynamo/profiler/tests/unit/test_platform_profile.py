# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from dynamo.profiler.utils.platform_profile import require_system_capacity

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


def test_missing_capacity_must_not_fall_back_to_h200(tmp_path):
    with pytest.raises(ValueError, match="H200"):
        require_system_capacity("ascend_910b3", [tmp_path], 65536)


def test_verified_capacity_does_not_claim_performance_database(tmp_path):
    (tmp_path / "ascend_910b3.yaml").write_text(f"gpu:\n  mem_capacity: {64*2**30}\n")
    result = require_system_capacity("ascend_910b3", [tmp_path], 65536)
    assert result["capacity_bytes"] == 64 * 2**30
    assert not result["performance_database_certified"]
    with pytest.raises(ValueError, match="exceeds"):
        require_system_capacity("ascend_910b3", [tmp_path], 32768)


def test_candidate_receipt_records_actual_parallelism_and_is_not_fpm(
    tmp_path, monkeypatch
):
    import json
    from types import SimpleNamespace

    from dynamo.common.platform.profile import (
        ProfileIdentity,
        validate_profile_manifest,
    )
    from dynamo.profiler.utils.platform_profile import record_candidate_profile

    identity = ProfileIdentity(
        platform="ascend",
        device_model="910B3",
        driver="25.2",
        sdk="9.1",
        engine="vllm",
        engine_version="0.23.0",
        plugin_version="0.23.0",
        model="Qwen3-0.6B",
        model_revision="config-sha256",
        precision="bf16",
        parallelism={"tp": 1},
        kv_config={"block_size": 128},
        topology={"nodes": 1},
        runtime_image="sha256:runtime",
    )
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(identity.model_dump_json())
    monkeypatch.setenv("DYN_PROFILE_IDENTITY", str(identity_path))
    work = tmp_path / "candidate"
    (work / "aiperf").mkdir(parents=True)
    (work / "config.yaml").write_text("spec: {}")
    (work / "aiperf/profile_export_aiperf.json").write_text("{}")
    record_candidate_profile(
        work, SimpleNamespace(tp=2, pp=1, dp=1, moe_tp=1, moe_ep=1)
    )
    receipt = json.loads((work / "profile-identity.json").read_text())
    assert receipt["identity"]["parallelism"]["tp"] == 2
    assert len(receipt["files"]) == 2
    with pytest.raises(ValueError, match="not Planner FPM"):
        validate_profile_manifest(work, receipt["identity"])


@pytest.mark.parametrize("platform", ["cuda", "ascend", "metax"])
def test_generated_planner_inherits_worker_platform(platform):
    from dynamo.profiler.utils.dgd_generation import _build_planner_config
    from dynamo.profiler.utils.dgdr_v1beta1_types import (
        DynamoGraphDeploymentRequestSpec,
    )

    dgdr = DynamoGraphDeploymentRequestSpec.model_validate(
        {
            "model": "Qwen/Qwen3-0.6B",
            "image": "locked:v1",
            "overrides": {
                "dgd": {
                    "apiVersion": "nvidia.com/v1beta1",
                    "kind": "DynamoGraphDeployment",
                    "spec": {
                        "components": [
                            {
                                "name": "VllmDecodeWorker",
                                "type": "worker",
                                "podTemplate": {
                                    "spec": {
                                        "containers": [
                                            {
                                                "name": "main",
                                                "env": [
                                                    {
                                                        "name": "DYN_PLATFORM",
                                                        "value": platform,
                                                    }
                                                ],
                                            }
                                        ]
                                    }
                                },
                            }
                        ]
                    },
                }
            },
        }
    )
    assert _build_planner_config(dgdr, None, None).target_platform == platform
    if platform != "cuda":
        from dynamo.planner.config.planner_config import PlannerConfig
        from dynamo.profiler.utils.dgdr_v1beta1_types import FeaturesSpec

        dgdr.features = FeaturesSpec(planner=PlannerConfig(target_platform="cuda"))
        with pytest.raises(ValueError, match="conflicts"):
            _build_planner_config(dgdr, None, None)
