# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os

from dynamo.common.constants import DisaggregationMode
from dynamo.common.platform import PlatformError, registry
from dynamo.vllm.platform import configure_platform_scheduler


def prepare_vllm_platform(config):
    platform_id = os.environ.get("DYN_PLATFORM")
    if platform_id is None:
        return None  # Legacy invocation; no vendor probing or behavior change.
    engine = config.engine_args
    if (
        config.headless
        or config.embedding_worker
        or config.disaggregation_mode != DisaggregationMode.AGGREGATED
        or engine.kv_transfer_config is not None
    ):
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "explicit platform integration requires aggregate generation",
        )
    if engine.pipeline_parallel_size != 1:
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "pipeline parallelism is not certified",
        )
    if (
        getattr(engine, "data_parallel_external_lb", False)
        or getattr(engine, "data_parallel_hybrid_lb", False)
        or getattr(engine, "data_parallel_backend", "mp") != "mp"
        or engine.data_parallel_size_local not in (None, engine.data_parallel_size)
    ):
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "explicit platform integration currently requires local multiprocessing DP",
        )
    configure_platform_scheduler(engine, config.use_kv_events)
    context = dict(
        engine="vllm",
        task="multimodal" if config.enable_multimodal else "text",
        tp=str(engine.tensor_parallel_size),
        dp=str(engine.data_parallel_size),
        eager=str(engine.enforce_eager).lower(),
        model=config.model,
        kv_events=str(config.use_kv_events).lower(),
        lora=str(engine.enable_lora).lower(),
        quantization=engine.quantization or "none",
        speculative=json.dumps(engine.speculative_config, sort_keys=True),
        expert_parallel=str(engine.enable_expert_parallel).lower(),
    )
    required = [
        "device.compute",
        "engine.multimodal" if config.enable_multimodal else "engine.text",
    ]
    if config.use_kv_events:
        required.append("kv.events")
    required.extend(
        name.strip()
        for name in os.environ.get("DYN_REQUIRED_CAPABILITIES", "").split(",")
        if name.strip()
    )
    descriptor = registry.prepare(platform_id, context, required)
    local_dp = engine.data_parallel_size_local or engine.data_parallel_size
    needed = engine.tensor_parallel_size * local_dp
    if needed > len(descriptor["devices"]):
        raise PlatformError(
            "insufficient_devices",
            platform_id,
            f"requires {needed} visible devices, found {len(descriptor['devices'])}",
        )
    return descriptor
