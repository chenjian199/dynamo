# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
from types import SimpleNamespace

from dynamo.common.constants import DisaggregationMode
from dynamo.common.platform import PlatformError, registry
from dynamo.vllm.kv_connector_protocols import ASCEND_MOONCAKE_MODULE
from dynamo.vllm.platform import configure_platform_scheduler

KVBM_MODULES = (
    "kvbm.vllm_integration.connector",
    "kvbm.vllm_integration.connector.dynamo_connector",
)


def validate_kvbm_configuration(config, platform_id, kv):
    engine = config.engine_args
    valid = (
        kv.kv_connector == "DynamoConnector"
        and getattr(kv, "kv_connector_module_path", None) in KVBM_MODULES
        and kv.kv_role == "kv_both"
        and engine.tensor_parallel_size in (1, 2)
        and engine.data_parallel_size == 1
        and engine.enforce_eager
        and not config.enable_multimodal
        and not engine.enable_lora
        and engine.quantization is None
        and engine.speculative_config is None
        and not engine.enable_expert_parallel
    )
    if not valid:
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "KVBM requires DynamoConnector and eager dense text TP1/TP2, DP1",
        )


def pd_transport(config, platform_id, kv):
    if kv.kv_connector != "PdConnector":
        return kv
    extra = kv.kv_connector_extra_config or {}
    children = extra.get("connectors", []) if isinstance(extra, dict) else []
    if (
        config.disaggregation_mode != DisaggregationMode.PREFILL
        or getattr(kv, "kv_connector_module_path", None) != KVBM_MODULES[0]
        or kv.kv_role != "kv_both"
        or not isinstance(children, list)
        or len(children) != 2
        or not all(isinstance(c, dict) for c in children)
    ):
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "KVBM PD requires one offload child then one transport on prefill",
        )
    offload = SimpleNamespace(
        **{
            name: children[0].get(name)
            for name in ("kv_connector", "kv_role", "kv_connector_module_path")
        }
    )
    validate_kvbm_configuration(config, platform_id, offload)
    transport = dict(children[1])
    transport.setdefault("kv_connector", None)
    transport.setdefault("kv_connector_module_path", None)
    transport.setdefault("kv_role", None)
    transport["kv_connector_extra_config"] = children[1].setdefault(
        "kv_connector_extra_config", {}
    )
    if not isinstance(transport["kv_connector_extra_config"], dict):
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "PD transport config must be a dict",
        )
    return SimpleNamespace(**transport)


def validate_pd_configuration(config, platform_id):
    """Validate our connector contract; native engines validate TP layout compatibility."""
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
        or type(engine.tensor_parallel_size) is not int
        or engine.tensor_parallel_size < 1
        or engine.data_parallel_size != 1
        or not engine.enforce_eager
        or config.enable_multimodal
        or engine.enable_lora
        or engine.speculative_config is not None
    ):
        raise PlatformError(
            "unsupported_configuration",
            platform_id,
            "PD requires a connector, positive TP, eager text DP1 without LoRA or speculative decoding",
        )
    kv = pd_transport(config, platform_id, kv)
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
                type(parallel.get("tp_size")) is int
                and parallel["tp_size"] > 0
                and parallel.get("dp_size") == 1
                and parallel.get("pp_size", 1) == 1
            )
        split = extra.get("heyi_tp_split", False)
        pair = (
            extra.get("prefill", {}).get("tp_size"),
            extra.get("decode", {}).get("tp_size"),
        )
        # This opt-in extension implements exactly a two-way split. Otherwise
        # leave TP ratios to the installed native connector, not a tested-pair list.
        valid = valid and type(split) is bool and (not split or pair == (1, 2))
        local_role = "prefill" if expected_role == "kv_producer" else "decode"
        valid = (
            valid
            and extra.get(local_role, {}).get("tp_size") == engine.tensor_parallel_size
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
    if platform_id == "metax" and kv.kv_connector == "MooncakeConnector":
        timeout = kv.kv_connector_extra_config.setdefault(
            "heyi_transfer_failure_timeout", 30
        )
        if type(timeout) not in (int, float) or not 5 <= timeout <= 300:
            raise PlatformError(
                "unsupported_configuration",
                platform_id,
                "Mooncake transfer failure timeout must be between 5 and 300 seconds",
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
    gms = os.environ.get("DYN_GMS_USE_V1") == "true"
    gms = gms or getattr(engine, "load_format", None) == "gms"
    if gms and platform_id == "ascend":
        raise PlatformError(
            "missing_capability",
            platform_id,
            "GMS requires enforced read-only VMM and POSIX handles; the tested "
            "910B3/CANN 9.1/HDK 26.1.1 stack rejects read-only access and "
            "POSIX allocation; Ascend GMS provider remains unavailable",
        )
    pd = config.disaggregation_mode in (
        DisaggregationMode.PREFILL,
        DisaggregationMode.DECODE,
    )
    if config.disaggregation_mode == DisaggregationMode.ENCODE:
        if (
            not config.enable_multimodal
            or engine.tensor_parallel_size != 1
            or engine.data_parallel_size != 1
            or not engine.enforce_eager
            or engine.kv_transfer_config is not None
        ):
            raise PlatformError(
                "unsupported_configuration",
                platform_id,
                "standalone encode requires eager multimodal TP1 DP1 without a KV connector",
            )
    connector = validate_pd_configuration(config, platform_id) if pd else None
    kvbm = (
        engine.kv_transfer_config is not None
        and engine.kv_transfer_config.kv_connector in ("DynamoConnector", "PdConnector")
    )
    if not pd and kvbm:
        validate_kvbm_configuration(config, platform_id, engine.kv_transfer_config)
    configure_platform_scheduler(engine, config.use_kv_events)
    task = "text"
    if config.embedding_worker:
        task = "embedding"
    elif config.enable_multimodal:
        task = "multimodal"
    context = dict(
        engine="vllm",
        task=task,
        tp=str(engine.tensor_parallel_size),
        pp=str(engine.pipeline_parallel_size),
        dp=str(engine.data_parallel_size),
        local_dp=str(engine.data_parallel_size_local),
        nnodes=str(getattr(engine, "nnodes", 1)),
        headless=str(config.headless).lower(),
        eager=str(engine.enforce_eager).lower(),
        model=config.model,
        kv_events=str(config.use_kv_events).lower(),
        lora=str(engine.enable_lora).lower(),
        quantization=engine.quantization or "none",
        speculative=json.dumps(engine.speculative_config, sort_keys=True),
        expert_parallel=str(engine.enable_expert_parallel).lower(),
        pd_connector=connector or "none",
        kvbm=str(kvbm).lower(),
        gms=str(gms).lower(),
    )
    required = [
        "device.compute",
        f"engine.{task}",
    ]
    if config.use_kv_events:
        required.append("kv.events")
    if pd:
        required.append("pd.transfer")
    if kvbm:
        required.append("kvbm.offload")
    if gms:
        required.append("gms.vmm")
    required.extend(
        name.strip()
        for name in os.environ.get("DYN_REQUIRED_CAPABILITIES", "").split(",")
        if name.strip()
    )
    configure_pd_environment(platform_id, connector)
    # Providers require visible devices. Placement belongs to vLLM: TP/PP may
    # span nodes and Ray may allocate remote devices, so global parallel sizes
    # cannot be compared with this process's local device count.
    return registry.prepare(platform_id, context, required)
