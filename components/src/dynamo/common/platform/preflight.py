# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os

from dynamo.common.constants import DisaggregationMode
from dynamo.common.platform import PlatformError, registry


def prepare_vllm_platform(config):
    platform_id = os.environ.get("DYN_PLATFORM")
    if platform_id is None:
        return None  # Legacy invocation; no vendor probing or behavior change.
    engine = config.engine_args
    if (
        config.headless
        or config.enable_multimodal
        or config.embedding_worker
        or config.disaggregation_mode != DisaggregationMode.AGGREGATED
        or engine.kv_transfer_config is not None
        or engine.enable_lora
        or engine.speculative_config is not None
    ):
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "explicit platform integration currently supports aggregate text only",
        )
    context = dict(
        engine="vllm",
        task="text",
        tp=str(engine.tensor_parallel_size),
        dp=str(engine.data_parallel_size),
        eager=str(engine.enforce_eager).lower(),
        model=config.model,
        kv_events=str(config.use_kv_events).lower(),
    )
    required = ["device.compute", "engine.text"]
    if config.use_kv_events:
        required.append("kv.events")
    required.extend(
        name.strip()
        for name in os.environ.get("DYN_REQUIRED_CAPABILITIES", "").split(",")
        if name.strip()
    )
    return registry.prepare(platform_id, context, required)
