# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# Derived from NVIDIA Dynamo. Modified by HEYI.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
from typing import Any

from dynamo.common.token_budget import TokenBudget, publish_token_budget

logger = logging.getLogger(__name__)


def _worker_kv_capacity(worker: Any) -> int | None:
    from vllm.v1.core.kv_cache_utils import get_max_concurrency_for_kv_cache_config

    cache = worker.model_runner.kv_cache_config
    if not cache.kv_cache_groups:
        return None
    config = worker.vllm_config
    return int(
        get_max_concurrency_for_kv_cache_config(config, cache)
        * config.model_config.max_model_len
    )


class KVCapacityWorkerExtension:
    """Primitive-only RPC for engines without a parent-process capacity field."""

    def get_dynamo_kv_capacity(self) -> int | None:
        return _worker_kv_capacity(self)


def configure_kv_capacity_worker(vllm_config: Any) -> None:
    if hasattr(vllm_config.cache_config, "kv_cache_size_tokens"):
        return
    parallel = vllm_config.parallel_config
    if not getattr(parallel, "worker_extension_cls", None):
        parallel.worker_extension_cls = "dynamo.vllm.capacity.KVCapacityWorkerExtension"


async def publish_kv_cache_capacity(runtime_config: Any, engine: Any) -> None:
    """Publish the engine's group-aware token capacity independently of KV events.

    Recent vLLM reports this per engine, even when num_gpu_blocks is summed
    across DP ranks. Event block widths describe routing hashes and cannot be
    multiplied by a shared hybrid block pool to estimate serving capacity.
    Older engines are queried on their workers using vLLM's own group-aware
    calculation. If neither interface exists, retain the legacy contract.
    """
    tokens = getattr(engine.vllm_config.cache_config, "kv_cache_size_tokens", None)
    if tokens is None:
        try:
            values = await engine.collective_rpc("get_dynamo_kv_capacity")
        except Exception as error:
            logger.warning(
                "Engine KV capacity query unavailable; retaining legacy capacity: %s",
                error,
            )
            return
        if not values or any(v is None for v in values):
            return
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in values):
            raise ValueError("vLLM worker KV capacity must be a positive integer")
        tokens = min(values)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError("vLLM kv_cache_size_tokens must be a nonnegative integer")
    if tokens == 0:
        # Pooling/encoder engines may have no KV cache to advertise.
        return
    runtime_config.set_engine_specific(
        "kv_cache_capacity",
        json.dumps({"tokens": tokens, "scope": "per_engine", "source": "vllm"}),
    )


def publish_vllm_token_budget(runtime_config: Any, max_model_len: int | None) -> None:
    """Publish vLLM's request-overflow contract to the Dynamo frontend."""
    if max_model_len is None:
        return
    publish_token_budget(
        runtime_config,
        TokenBudget(
            combined_limit=max_model_len,
            reject_prompt_overflow=True,
            reject_total_overflow=True,
        ),
    )


def per_rank_kv_blocks(
    total_kv_blocks: int | None, data_parallel_size: int
) -> int | None:
    if total_kv_blocks is None:
        return None

    if data_parallel_size <= 1 or total_kv_blocks <= 0:
        return total_kv_blocks

    per_rank = total_kv_blocks // data_parallel_size
    if per_rank == 0:
        logger.warning(
            "vLLM reported fewer total KV blocks (%s) than DP ranks (%s); "
            "publishing 1 block per rank",
            total_kv_blocks,
            data_parallel_size,
        )
        return 1

    remainder = total_kv_blocks % data_parallel_size
    if remainder:
        logger.warning(
            "vLLM reported aggregate KV blocks (%s) not divisible by DP ranks (%s); "
            "publishing floor per-rank capacity %s",
            total_kv_blocks,
            data_parallel_size,
            per_rank,
        )

    return per_rank


def get_metrics_model_name(config: Any) -> str:
    return str(
        getattr(config, "served_model_name", None) or getattr(config, "model", "")
    )


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else None
    if hasattr(value, "__dict__"):
        return vars(value)
    return None


def get_spec_decode_runtime_data(
    config: Any, vllm_config: Any
) -> dict[str, Any] | None:
    spec_config = getattr(vllm_config, "speculative_config", None)
    if spec_config is None:
        engine_args = getattr(config, "engine_args", None)
        spec_config = getattr(engine_args, "speculative_config", None)
    spec = _as_mapping(spec_config)
    if not spec:
        return None

    try:
        nextn = int(spec.get("num_speculative_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if nextn <= 0:
        return None

    data: dict[str, Any] = {"nextn": nextn, "source": "backend_config"}
    method = spec.get("method")
    if method:
        data["method"] = str(method)
    return data
