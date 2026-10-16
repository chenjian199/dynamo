# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dynamo.common.constants import DisaggregationMode
from dynamo.common.platform.registry import PlatformError
from dynamo.vllm import main as entry
from dynamo.vllm.handlers import BaseWorkerHandler
from dynamo.vllm.instrumented_scheduler import InstrumentedScheduler
from dynamo.vllm.platform import (
    configure_platform_scheduler,
    publish_platform_engine_metadata,
    validate_platform_engine_config,
)
from dynamo.vllm.worker_factory import _DecodeWorkerLifecycle

pytestmark = [
    pytest.mark.unit,
    pytest.mark.vllm,
    pytest.mark.core,
    pytest.mark.gpu_0,
    pytest.mark.pre_merge,
]


def test_scheduler_preserves_custom_implementation_and_rejects_incompatible_kv(
    monkeypatch,
):
    monkeypatch.setenv("DYN_PLATFORM", "ascend")
    args = SimpleNamespace(scheduler_cls="vendor.scheduler.Custom")
    configure_platform_scheduler(args, True)
    assert args.scheduler_cls == "vendor.scheduler.Custom"
    config = SimpleNamespace(
        parallel_config=SimpleNamespace(pipeline_parallel_size=1),
        kv_events_config=SimpleNamespace(enable_kv_cache_events=True, publisher="zmq"),
        cache_config=SimpleNamespace(enable_prefix_caching=True),
        scheduler_config=SimpleNamespace(scheduler_cls=object),
    )
    with pytest.raises(PlatformError, match="incompatible_scheduler"):
        validate_platform_engine_config(config)

    class Compatible(InstrumentedScheduler):
        pass

    config.scheduler_config.scheduler_cls = Compatible
    validate_platform_engine_config(config)
    config.cache_config.enable_prefix_caching = False
    with pytest.raises(PlatformError, match="prefix caching"):
        validate_platform_engine_config(config)
    config.kv_events_config = None
    config.scheduler_config.scheduler_cls = object
    with pytest.raises(PlatformError, match="incompatible_scheduler"):
        validate_platform_engine_config(config, require_metrics=True)


def test_registration_records_effective_precision_and_parallelism(monkeypatch):
    monkeypatch.setenv("DYN_PLATFORM", "ascend")
    runtime = Mock()
    config = SimpleNamespace(
        model_config=SimpleNamespace(
            dtype="bfloat16",
            quantization="compressed-tensors",
            is_multimodal_model=False,
        ),
        parallel_config=SimpleNamespace(
            tensor_parallel_size=2, data_parallel_size=2, enable_expert_parallel=True
        ),
        cache_config=SimpleNamespace(cache_dtype="auto"),
        lora_config=None,
        speculative_config=SimpleNamespace(
            method="draft_model",
            num_speculative_tokens=3,
            draft_model_config=SimpleNamespace(model="test-draft"),
            draft_parallel_config=SimpleNamespace(tensor_parallel_size=1),
        ),
    )
    publish_platform_engine_metadata(runtime, config)
    name, encoded = runtime.set_engine_specific.call_args.args
    assert name == "platform_engine"
    assert '"quantization": "compressed-tensors"' in encoded
    assert '"tensor_parallel_size": 2' in encoded
    assert '"expert_parallel": true' in encoded
    assert '"draft_model": "test-draft"' in encoded
    assert '"draft_tensor_parallel_size": 1' in encoded


def test_lifecycle_cleans_partial_state_once_even_on_error():
    handler, engine, resource = Mock(), Mock(), Mock()
    handler.cleanup.side_effect = RuntimeError("publisher failure")
    state = _DecodeWorkerLifecycle(
        engine_client=engine,
        vllm_config=SimpleNamespace(shutdown_timeout=3),
        handler=handler,
        cleanup_resource=resource,
    )
    with pytest.raises(RuntimeError, match="publisher failure"):
        state.cleanup()
    state.cleanup()
    handler.cleanup.assert_called_once()
    engine.shutdown.assert_called_once_with(timeout=3)
    resource.cleanup.assert_called_once()


def test_handler_stops_all_producers_before_resource_release():
    order = []
    publisher, relay, directory = Mock(), Mock(), Mock()
    publisher.shutdown.side_effect = lambda: order.append("kv")
    relay.shutdown.side_effect = lambda: order.append("fpm")
    directory.cleanup.side_effect = lambda: order.append("directory")
    handler = SimpleNamespace(
        temp_dirs=[directory],
        kv_publishers=[publisher],
        fpm_relays=[relay],
        _ep_capacity_executor=None,
        _custom_encoder=None,
    )
    BaseWorkerHandler.cleanup(handler)
    BaseWorkerHandler.cleanup(handler)
    assert order == ["fpm", "kv", "directory"]


@pytest.mark.parametrize("failure", ["config", "engine", "cache"])
def test_startup_failure_releases_acquired_resources(monkeypatch, tmp_path, failure):
    monkeypatch.delenv("DYN_PLATFORM", raising=False)
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    directory = Mock(name=str(tmp_path / "metrics"))
    directory.name = str(tmp_path / "metrics")
    monkeypatch.setattr(
        entry.tempfile, "TemporaryDirectory", Mock(return_value=directory)
    )
    monkeypatch.setattr(entry, "setup_multiprocess_prometheus", Mock())
    monkeypatch.setattr(entry, "LLMBackendMetrics", Mock())
    monkeypatch.setattr(entry, "configure_multimodal_embedding_cache", Mock())
    monkeypatch.setattr(
        entry, "disable_hybrid_kv_cache_manager_for_incompatible_pd_connector", Mock()
    )
    monkeypatch.setattr(entry, "_uses_dynamo_connector", lambda args: False)
    model_config = SimpleNamespace(get_diff_sampling_param=lambda: {})
    effective = SimpleNamespace(
        model_config=model_config,
        additional_config={},
        cache_config=SimpleNamespace(block_size=64),
    )
    engine = Mock()
    create = Mock(
        return_value=engine,
        side_effect=RuntimeError("engine") if failure == "engine" else None,
    )
    monkeypatch.setattr(entry.AsyncLLM, "from_vllm_config", create)
    cache = Mock(side_effect=RuntimeError("cache"))
    monkeypatch.setattr(entry, "get_engine_cache_info", cache)
    args = SimpleNamespace(
        enable_lora=False,
        load_format="auto",
        enable_log_requests=False,
        disable_log_stats=False,
        create_engine_config=Mock(
            return_value=effective,
            side_effect=RuntimeError("config") if failure == "config" else None,
        ),
    )
    config = SimpleNamespace(
        engine_args=args,
        disaggregation_mode=DisaggregationMode.AGGREGATED,
        embedding_worker=False,
        served_model_name="test",
        component="backend",
        route_to_encoder=False,
        multimodal_embedding_cache_capacity_gb=0,
        namespace="test",
    )
    with pytest.raises(RuntimeError, match=failure):
        entry.setup_vllm_engine(config)
    directory.cleanup.assert_called_once()
    assert "PROMETHEUS_MULTIPROC_DIR" not in entry.os.environ
    if failure == "cache":
        engine.shutdown.assert_called_once()
    else:
        engine.shutdown.assert_not_called()
