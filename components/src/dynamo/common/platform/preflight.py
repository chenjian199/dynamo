# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os

from dynamo.common.constants import DisaggregationMode
from dynamo.common.platform import PlatformError, registry
from dynamo.vllm.kv_connector_protocols import ASCEND_MOONCAKE_MODULE
from dynamo.vllm.platform import configure_platform_scheduler


def validate_pd_configuration(config, platform_id):
    """Bound the initial vendor PD integration to homogeneous text TP1/DP1."""
    engine = config.engine_args
    kv = engine.kv_transfer_config
    expected_role = (
        "kv_producer"
        if config.disaggregation_mode == DisaggregationMode.PREFILL
        else "kv_consumer"
    )
    if (
        config.disaggregation_mode
        not in (DisaggregationMode.PREFILL, DisaggregationMode.DECODE)
        or kv is None
        or engine.tensor_parallel_size != 1
        or engine.data_parallel_size != 1
        or not engine.enforce_eager
        or config.enable_multimodal
        or engine.enable_lora
        or engine.quantization is not None
        or engine.speculative_config is not None
        or engine.enable_expert_parallel
    ):
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "PD requires a connector and eager text TP1/DP1 without advanced engine features",
        )
    module = getattr(kv, "kv_connector_module_path", None)
    if platform_id == "ascend":
        valid = (
            kv.kv_connector == "MooncakeConnector"
            and module == ASCEND_MOONCAKE_MODULE
            and kv.kv_role == expected_role
        )
        extra = kv.kv_connector_extra_config
        for role in ("prefill", "decode"):
            parallel = extra.get(role, {})
            valid = valid and (
                parallel.get("tp_size") == 1
                and parallel.get("dp_size") == 1
                and parallel.get("pp_size", 1) == 1
            )
    elif platform_id == "metax":
        valid = (
            kv.kv_connector == "MooncakeConnector"
            and module is None
            and kv.kv_role == expected_role
            and kv.kv_connector_extra_config.get("mooncake_protocol") == "maca"
        ) or (
            kv.kv_connector == "NixlConnector"
            and module is None
            and kv.kv_role == "kv_both"
        )
    else:
        valid = (
            platform_id == "cuda"
            and kv.kv_connector == "NixlConnector"
            and module is None
            and kv.kv_role == "kv_both"
        )
    if not valid:
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "PD connector, module, role or peer parallel configuration is unsupported",
        )
    return kv.kv_connector


def configure_pd_environment(platform_id, connector):
    """Select MACA-aware UCX protocols before NIXL loads its native libraries."""
    if platform_id != "metax" or connector != "NixlConnector":
        return
    # The UCX 1.14 library bundled with NIXL 1.0.0+maca defaults to the old
    # protocol path. Its emulated GET packs a device pointer with host memcpy.
    # Protocol v2 handles MACA memory correctly; keep peer error handling enabled.
    if os.environ.get("UCX_PROTO_ENABLE", "y").lower() not in ("y", "yes", "1"):
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "MetaX NIXL requires UCX_PROTO_ENABLE=y to avoid unsafe GPU GET",
        )
    os.environ.setdefault("UCX_PROTO_ENABLE", "y")
    os.environ.setdefault("UCX_TLS", "maca_ipc,maca_copy,tcp")


def prepare_vllm_platform(config):
    platform_id = os.environ.get("DYN_PLATFORM")
    if platform_id is None:
        return None  # Legacy invocation; no vendor probing or behavior change.
    engine = config.engine_args
    if config.headless or config.embedding_worker:
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "explicit platform integration requires generation workers",
        )
    pd = config.disaggregation_mode != DisaggregationMode.AGGREGATED
    connector = validate_pd_configuration(config, platform_id) if pd else None
    if not pd and engine.kv_transfer_config is not None:
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "aggregate KV transfer is not certified",
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
        pd_connector=connector or "none",
    )
    required = [
        "device.compute",
        "engine.multimodal" if config.enable_multimodal else "engine.text",
    ]
    if config.use_kv_events:
        required.append("kv.events")
    if pd:
        required.append("pd.transfer")
    required.extend(
        name.strip()
        for name in os.environ.get("DYN_REQUIRED_CAPABILITIES", "").split(",")
        if name.strip()
    )
    configure_pd_environment(platform_id, connector)
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
