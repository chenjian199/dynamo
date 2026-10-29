# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys
from types import SimpleNamespace

import pytest

from dynamo.common.constants import DisaggregationMode
from dynamo.common.platform import preflight
from dynamo.common.platform.registry import PlatformError, PlatformRegistry

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


def test_cpu_does_not_import_vendor_modules():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
class BlockVendors:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in ('torch', 'vllm', 'pymxsml', 'pynvml', 'vllm_metax', 'torch_npu', 'acl'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockVendors())
from dynamo.common.platform import registry
assert registry.prepare('cpu')['device_kind'] == 'cpu'
""",
        ],
        check=True,
    )


def test_registry_identity_is_frozen_and_copies_are_isolated():
    registry = PlatformRegistry()
    descriptor = registry.prepare("cpu")
    descriptor["software"]["mutation"] = "bad"
    assert registry.prepare("cpu")["software"] == {}
    with pytest.raises(PlatformError, match="configuration_conflict"):
        registry.prepare("metax")
    with pytest.raises(PlatformError, match="configuration_conflict"):
        registry.prepare("cpu", {"model": "different"})


def test_provider_errors_are_explicit():
    registry = PlatformRegistry()
    with pytest.raises(PlatformError, match="unknown_platform"):
        registry.prepare("typo")
    with pytest.raises(PlatformError, match="duplicate_provider"):
        registry.register("metax", lambda: None)

    def broken_loader():
        raise ImportError("missing SDK")

    registry.register("broken", broken_loader)
    with pytest.raises(PlatformError, match="probe_failed") as error:
        registry.prepare("broken")
    assert error.value.to_dict()["retryable"] is False


@pytest.mark.parametrize("state", ["unknown", "unavailable", "available"])
def test_required_capability_conditions(state):
    registry = PlatformRegistry()
    registry.register(
        "test",
        lambda: lambda context: {
            "schema_version": 1,
            "platform_id": "test",
            "capabilities": {
                "test.feature": {
                    "state": state,
                    "reason": "fixture",
                    "conditions": {"tp": "1"},
                }
            },
        },
    )
    if state == "available":
        registry.prepare("test", {"tp": "1"}, ["test.feature"])
    else:
        with pytest.raises(PlatformError, match="missing_capability"):
            registry.prepare("test", {"tp": "1"}, ["test.feature"])
    registry = PlatformRegistry()
    registry.register(
        "test",
        lambda: lambda context: {
            "schema_version": 1,
            "platform_id": "test",
            "capabilities": {
                "test.feature": {"state": "available", "conditions": {"tp": "1"}}
            },
        },
    )
    with pytest.raises(PlatformError, match="capability_mismatch"):
        registry.prepare("test", {"tp": "2"}, ["test.feature"])


@pytest.fixture
def engine_config():
    return SimpleNamespace(
        headless=False,
        enable_multimodal=False,
        embedding_worker=False,
        disaggregation_mode=DisaggregationMode.AGGREGATED,
        model="Qwen3-0.6B",
        use_kv_events=True,
        engine_args=SimpleNamespace(
            kv_transfer_config=None,
            enable_lora=False,
            speculative_config=None,
            tensor_parallel_size=1,
            data_parallel_size=1,
            enforce_eager=True,
            pipeline_parallel_size=1,
            scheduler_cls=None,
            quantization=None,
            enable_expert_parallel=False,
            data_parallel_size_local=None,
        ),
    )


def test_preflight_legacy_and_explicit_requirements(monkeypatch, engine_config):
    monkeypatch.delenv("DYN_PLATFORM", raising=False)
    assert preflight.prepare_vllm_platform(None) is None
    monkeypatch.setenv("DYN_PLATFORM", "metax")
    monkeypatch.setenv("DYN_REQUIRED_CAPABILITIES", " pd.transfer, ")
    config = engine_config
    observed = {}

    def prepare(platform_id, context, required):
        observed.update(platform_id=platform_id, context=context, required=required)
        return {"platform_id": platform_id, "devices": [{"ordinal": 0}]}

    monkeypatch.setattr(preflight.registry, "prepare", prepare)
    assert preflight.prepare_vllm_platform(config)["platform_id"] == "metax"
    assert observed["required"] == [
        "device.compute",
        "engine.text",
        "kv.events",
        "pd.transfer",
    ]
    config.disaggregation_mode = DisaggregationMode.PREFILL
    with pytest.raises(PlatformError, match="unsupported_configuration"):
        preflight.prepare_vllm_platform(config)


@pytest.mark.parametrize("platform_id", ["cuda", "metax", "ascend"])
@pytest.mark.parametrize(
    "engine_overrides,headless",
    [
        ({"tensor_parallel_size": 8}, False),
        ({"tensor_parallel_size": 16, "nnodes": 2}, False),
        ({"pipeline_parallel_size": 2, "nnodes": 2}, True),
        ({"pipeline_parallel_size": 2}, False),
        ({"data_parallel_size": 8, "data_parallel_size_local": 1}, False),
        ({"data_parallel_external_lb": True}, False),
        ({"data_parallel_hybrid_lb": True}, False),
        (
            {"data_parallel_backend": "ray", "distributed_executor_backend": "ray"},
            False,
        ),
        ({"enforce_eager": False}, False),
    ],
)
def test_native_topology_is_validated_by_engine(
    monkeypatch, engine_config, platform_id, engine_overrides, headless
):
    monkeypatch.setenv("DYN_PLATFORM", platform_id)
    monkeypatch.delenv("DYN_REQUIRED_CAPABILITIES", raising=False)
    monkeypatch.delenv("DYN_GMS_USE_V1", raising=False)
    engine_config.headless = headless
    engine_config.use_kv_events = False
    vars(engine_config.engine_args).update(engine_overrides)
    before = vars(engine_config.engine_args).copy()
    observed = {}

    def prepare(platform_id, context, required):
        observed.update(context)
        # Remote ranks are not represented in local device enumeration.
        return {"platform_id": platform_id, "devices": [{"ordinal": 0}]}

    monkeypatch.setattr(preflight.registry, "prepare", prepare)
    assert preflight.prepare_vllm_platform(engine_config)["platform_id"] == platform_id
    assert vars(engine_config.engine_args) == before
    assert observed["pp"] == str(engine_config.engine_args.pipeline_parallel_size)
    assert observed["headless"] == str(headless).lower()


def test_native_aggregate_connector_is_not_treated_as_dynamo_offload(
    monkeypatch, engine_config
):
    monkeypatch.setenv("DYN_PLATFORM", "cuda")
    monkeypatch.delenv("DYN_REQUIRED_CAPABILITIES", raising=False)
    monkeypatch.delenv("DYN_GMS_USE_V1", raising=False)
    engine_config.engine_args.kv_transfer_config = SimpleNamespace(
        kv_connector="NativeConnector"
    )
    observed = {}

    def prepare(platform_id, context, required):
        observed.update(context=context, required=required)
        return {"devices": [{}]}

    monkeypatch.setattr(preflight.registry, "prepare", prepare)
    preflight.prepare_vllm_platform(engine_config)
    assert observed["context"]["kvbm"] == "false"
    assert "kvbm.offload" not in observed["required"]
