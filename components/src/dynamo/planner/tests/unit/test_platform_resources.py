# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy

import pytest
from dynamo.common.platform.kubernetes import DEVICE_RESOURCES, scalar_device_count
from dynamo.planner.errors import GPUShapeUnavailableError
from dynamo.planner.monitoring.dgd_services import Service
from dynamo.profiler.utils.config import Component, setup_worker_component_resources

pytestmark = [
    pytest.mark.unit,
    pytest.mark.gpu_0,
    pytest.mark.pre_merge,
    pytest.mark.planner,
]


def worker(platform):
    return {
        "name": "worker",
        "type": "decode",
        "podTemplate": {
            "spec": {
                "containers": [
                    {
                        "name": "main",
                        "image": "locked:v1",
                        "env": [{"name": "DYN_PLATFORM", "value": platform}],
                        "resources": {
                            "limits": {DEVICE_RESOURCES[platform]: "2", "cpu": "4"},
                            "requests": {DEVICE_RESOURCES[platform]: "2"},
                        },
                    }
                ]
            }
        },
    }


@pytest.mark.parametrize("platform", DEVICE_RESOURCES)
def test_count_multinode_and_profiler_preserve_platform(platform):
    component = worker(platform)
    component["multinode"] = {"nodeCount": 2}
    svc = Service(name="worker", service=component)
    assert svc.get_gpu_count() == 2
    assert svc.get_gpu_shape({}).gpus_per_replica == 4
    model = Component.model_validate(component)
    setup_worker_component_resources(model, 4)
    container = model.podTemplate.spec.containers[0]
    assert container.resources["limits"] == {
        DEVICE_RESOURCES[platform]: "4",
        "cpu": "4",
    }
    assert container.resources["requests"] == {DEVICE_RESOURCES[platform]: "4"}
    assert container.image == "locked:v1"


@pytest.mark.parametrize("bad", [True, False, 1.5, "1.5", "-1", "0", None])
def test_reject_invalid_count(bad):
    container = worker("ascend")["podTemplate"]["spec"]["containers"][0]
    container["resources"] = {"limits": {DEVICE_RESOURCES["ascend"]: bad}}
    with pytest.raises(ValueError, match="Invalid GPU count"):
        scalar_device_count(container)


@pytest.mark.parametrize("platform", ["ascend", "metax"])
def test_missing_identity_conflict_and_limits_mismatch(platform):
    container = worker(platform)["podTemplate"]["spec"]["containers"][0]
    original = copy.deepcopy(container)
    container.pop("env")
    with pytest.raises(ValueError, match="DYN_PLATFORM"):
        scalar_device_count(container)
    container = copy.deepcopy(original)
    container["resources"]["limits"]["nvidia.com/gpu"] = "1"
    with pytest.raises(ValueError, match="mixes"):
        scalar_device_count(container)
    container = copy.deepcopy(original)
    container["resources"]["requests"][DEVICE_RESOURCES[platform]] = "1"
    with pytest.raises(ValueError, match="must match"):
        scalar_device_count(container)


@pytest.mark.parametrize("platform", DEVICE_RESOURCES)
def test_auxiliary_device_requires_operator_status(platform):
    component = worker(platform)
    auxiliary = copy.deepcopy(component["podTemplate"]["spec"]["containers"][0])
    auxiliary["name"] = "sidecar"
    component["podTemplate"]["spec"]["initContainers"] = [auxiliary]
    svc = Service(name="worker", service=component)
    with pytest.raises(GPUShapeUnavailableError):
        svc.get_gpu_shape({})
    deployment = {
        "metadata": {"generation": 2},
        "status": {
            "observedGeneration": 2,
            "components": {"worker": {"gpusPerEngine": 2, "gpusPerReplica": 4}},
        },
    }
    assert svc.get_gpu_shape(deployment).gpus_per_replica == 4
    deployment["status"]["observedGeneration"] = 1
    with pytest.raises(GPUShapeUnavailableError):
        svc.get_gpu_shape(deployment)


@pytest.mark.parametrize("platform", ["ascend", "metax"])
def test_vendor_does_not_use_native_cuda_perf_config(platform):
    from dynamo.planner.config.planner_config import AICPerfModelSpec, PlannerConfig
    from dynamo.planner.core.perf_model.aic_adapter import PlannerEnginePerfModel

    config = PlannerConfig(
        target_platform=platform,
        mode="agg",
        aic_perf_model=AICPerfModelSpec(
            hf_id="Qwen/Qwen3-0.6B",
            system="h200_sxm",
            backend="vllm",
            decode_pick={"tp": 1, "pp": 1, "dp": 1},
        ),
    )
    adapter = PlannerEnginePerfModel(
        worker_type="aggregated", config=config, capabilities=None
    )
    assert adapter._build_aic_config() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", [10422, None])
async def test_engine_capacity_not_routing_hash_width(reported):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from dynamo.planner.config.defaults import SubComponentType
    from dynamo.planner.connectors.mdc import MdcEntry, worker_info_from_mdc
    from dynamo.vllm.capacity import publish_kv_cache_capacity

    runtime = Mock()
    engine = SimpleNamespace(
        vllm_config=SimpleNamespace(
            cache_config=SimpleNamespace(kv_cache_size_tokens=reported)
        ),
        collective_rpc=AsyncMock(return_value=[10422, 11000]),
    )
    await publish_kv_cache_capacity(runtime, engine)
    key, value = runtime.set_engine_specific.call_args.args
    card = dict(
        kv_cache_block_size=256,
        runtime_config={
            "total_kv_blocks": 4285,
            "runtime_data": {key: json.loads(value)},
        },
    )
    info = worker_info_from_mdc(
        MdcEntry(card_json=card), SubComponentType.DECODE, "vllm"
    )
    assert info.max_kv_tokens == 10422
    assert info.kv_cache_block_size == 256
    if reported is not None:
        engine.collective_rpc.assert_not_called()
    else:
        engine.collective_rpc.assert_awaited_once_with("get_dynamo_kv_capacity")
    card["runtime_config"]["runtime_data"] = {}
    legacy = worker_info_from_mdc(
        MdcEntry(card_json=card), SubComponentType.DECODE, "vllm"
    )
    assert legacy.max_kv_tokens == 4285 * 256


@pytest.mark.parametrize(
    "capacity",
    [
        {"tokens": 0, "scope": "per_engine"},
        {"tokens": True, "scope": "per_engine"},
        {"tokens": 10422, "scope": "aggregate"},
        {"tokens": "10422", "scope": "per_engine"},
    ],
)
def test_reject_invalid_engine_capacity_metadata(capacity):
    from dynamo.planner.connectors.mdc import _parse_kv_capacity_tokens

    with pytest.raises(ValueError, match="KV capacity metadata"):
        _parse_kv_capacity_tokens({"runtime_data": {"kv_cache_capacity": capacity}})


@pytest.mark.asyncio
async def test_legacy_engine_without_capacity_rpc_keeps_existing_contract():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from dynamo.vllm.capacity import publish_kv_cache_capacity

    runtime = Mock()
    engine = SimpleNamespace(
        vllm_config=SimpleNamespace(cache_config=SimpleNamespace()),
        collective_rpc=AsyncMock(side_effect=NotImplementedError("old engine")),
    )
    await publish_kv_cache_capacity(runtime, engine)
    runtime.set_engine_specific.assert_not_called()


@pytest.mark.asyncio
async def test_engine_without_kv_cache_does_not_publish_false_capacity():
    from types import SimpleNamespace
    from unittest.mock import Mock

    from dynamo.vllm.capacity import publish_kv_cache_capacity

    runtime = Mock()
    engine = SimpleNamespace(
        vllm_config=SimpleNamespace(
            cache_config=SimpleNamespace(kv_cache_size_tokens=0)
        )
    )
    await publish_kv_cache_capacity(runtime, engine)
    runtime.set_engine_specific.assert_not_called()


@pytest.mark.parametrize(
    "native,custom", [(True, ""), (False, ""), (False, "user.Extension")]
)
def test_capacity_worker_preserves_native_and_custom_extensions(native, custom):
    from types import SimpleNamespace
    from dynamo.vllm.capacity import configure_kv_capacity_worker

    cache = SimpleNamespace(**({"kv_cache_size_tokens": None} if native else {}))
    config = SimpleNamespace(
        cache_config=cache, parallel_config=SimpleNamespace(worker_extension_cls=custom)
    )
    configure_kv_capacity_worker(config)
    assert config.parallel_config.worker_extension_cls == (
        custom if native or custom else "dynamo.vllm.capacity.KVCapacityWorkerExtension"
    )
