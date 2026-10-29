# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Platform checks for the existing vLLM entry point, without a second lifecycle."""

import importlib
import json
import os

from dynamo.common.platform.registry import PlatformError

INSTRUMENTED_SCHEDULER = "dynamo.vllm.instrumented_scheduler.InstrumentedScheduler"


def configure_platform_scheduler(engine_args, use_kv_events):
    """Preserve custom schedulers; require the invalidation extension for KV mode."""
    if not use_kv_events:
        return
    if engine_args.scheduler_cls is None:
        engine_args.scheduler_cls = INSTRUMENTED_SCHEDULER


def validate_platform_engine_config(vllm_config, *, require_metrics=False):
    """Check the effective config after the vendor has applied its defaults.

    This runs before AsyncLLM starts processes. In particular a vendor must not
    silently replace the scheduler that publishes idle cache invalidations.
    """
    platform_id = os.environ.get("DYN_PLATFORM")
    if platform_id is None:
        return
    events = vllm_config.kv_events_config
    use_events = events is not None and events.enable_kv_cache_events
    if not use_events and not require_metrics:
        return
    if use_events and not vllm_config.cache_config.enable_prefix_caching:
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "KV routing requires prefix caching",
        )
    if use_events and events.publisher != "zmq":
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "KV routing requires the ZMQ event publisher",
        )
    scheduler = vllm_config.scheduler_config.scheduler_cls
    if isinstance(scheduler, str):
        module, name = scheduler.rsplit(".", 1)
        scheduler = getattr(importlib.import_module(module), name)
    # Optional backend import is deliberately delayed until a vLLM worker opts in.
    base = getattr(
        importlib.import_module("dynamo.vllm.instrumented_scheduler"),
        "InstrumentedScheduler",
    )
    if not isinstance(scheduler, type) or not issubclass(scheduler, base):
        raise PlatformError(
            "incompatible_scheduler",
            platform_id,
            "KV routing and forward-pass metrics require InstrumentedScheduler or a compatible subclass; "
            "the configured scheduler will not be overwritten",
        )


def publish_platform_engine_metadata(runtime_config, vllm_config):
    """Record the effective model configuration only after engine initialization."""
    platform_id = os.environ.get("DYN_PLATFORM")
    if platform_id is None:
        return
    model = vllm_config.model_config
    parallel = vllm_config.parallel_config
    speculative = vllm_config.speculative_config
    draft_model = getattr(speculative, "draft_model_config", None)
    draft_parallel = getattr(speculative, "draft_parallel_config", None)
    metadata = dict(
        platform=platform_id,
        dtype=str(model.dtype),
        kv_cache_dtype=vllm_config.cache_config.cache_dtype,
        quantization=model.quantization,
        tensor_parallel_size=parallel.tensor_parallel_size,
        pipeline_parallel_size=parallel.pipeline_parallel_size,
        data_parallel_size=parallel.data_parallel_size,
        expert_parallel=parallel.enable_expert_parallel,
        lora=vllm_config.lora_config is not None,
        speculative_method=speculative.method if speculative is not None else None,
        draft_model=getattr(draft_model, "model", None),
        draft_tensor_parallel_size=getattr(
            draft_parallel, "tensor_parallel_size", None
        ),
        num_speculative_tokens=getattr(speculative, "num_speculative_tokens", None),
        multimodal=model.is_multimodal_model,
    )
    runtime_config.set_engine_specific("platform_engine", json.dumps(metadata))
