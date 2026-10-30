# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fail closed on incompatible explicit-platform PD peers before engine submission.

This control-plane envelope is removed before passing metadata to the connector.
The connector remains responsible for actual layout negotiation and buffer lifetime.
"""

import hashlib
import importlib.metadata
import json
import os

from dynamo.llm.exceptions import InvalidArgument
from dynamo.vllm.kv_connector_protocols import resolve_pd_connector_config

CONTRACT_KEY = "_dynamo_pd_contract"


def build_pd_contract(config, platform_id):
    model = config.model_config
    hf_config = model.hf_config.to_dict()
    # Library metadata and mount locations do not describe the tensor layout.
    for key in ("_name_or_path", "transformers_version"):
        hf_config.pop(key, None)
    # Offload is local to the prefill worker. Peers must agree on the child
    # transport's wire contract, not on the presence of a local cache wrapper.
    kv = resolve_pd_connector_config(config).kv_transfer_config
    parallel = config.parallel_config
    software = {"vllm": importlib.metadata.version("vllm")}
    if platform_id in ("metax", "ascend"):
        software["plugin"] = importlib.metadata.version("vllm-" + platform_id)
    return {
        "schema": 2,
        "platform": platform_id,
        "software": software,
        "model": model.model,
        "revision": model.revision,
        "config_sha256": hashlib.sha256(
            json.dumps(hf_config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "dtype": str(model.dtype),
        "cache_dtype": config.cache_config.cache_dtype,
        "block_size": config.cache_config.block_size,
        "tp": parallel.tensor_parallel_size,
        # Deprecated schema-2 hint retained for older readers' exact matching.
        # It is no longer used to authorize TP ratios or model architectures.
        "tp_reshard": (
            "mha-gqa"
            if all(
                type(hf_config.get(key)) is int
                and hf_config[key] > 0
                and hf_config[key] % 2 == 0
                for key in ("num_attention_heads", "num_key_value_heads")
            )
            and hf_config.get("model_type") in ("qwen3", "llama")
            else None
        ),
        "dp": parallel.data_parallel_size,
        "pp": parallel.pipeline_parallel_size,
        "connector": kv.kv_connector,
        "connector_module": kv.kv_connector_module_path,
        "layout_override": os.environ.get("VLLM_KV_CACHE_LAYOUT"),
    }


def attach_pd_contract(params, contract):
    if contract is None:
        return params
    if not isinstance(params, dict):
        raise InvalidArgument("Prefill did not return KV transfer metadata")
    return {**params, CONTRACT_KEY: contract}


def _compatible_contract(peer, local):
    if not isinstance(peer, dict):
        return False
    if local.get("schema") != 2:
        return peer == local  # Preserve legacy exact-match contracts.
    if any(type(c.get("tp")) is not int or c["tp"] < 1 for c in (peer, local)):
        return False
    # TP layout negotiation belongs to the engine connector. The old schema-2
    # tp_reshard field was a certification hint, not a tensor-layout property.
    # Ignore it for rolling compatibility, but retain every other contract field.
    ignored = {"tp", "tp_reshard"}
    return {k: v for k, v in peer.items() if k not in ignored} == {
        k: v for k, v in local.items() if k not in ignored
    }


def validate_pd_handoff(params, contract):
    if contract is None:
        return params
    if not isinstance(params, dict) or not _compatible_contract(
        params.get(CONTRACT_KEY), contract
    ):
        peer = params.get(CONTRACT_KEY) if isinstance(params, dict) else None
        differences = (
            sorted(
                key
                for key in peer.keys() | contract.keys()
                if peer.get(key) != contract.get(key)
            )
            if isinstance(peer, dict)
            else ["missing_or_invalid_contract"]
        )
        raise InvalidArgument(
            "Incompatible PD peer: platform, engine version, model, precision, "
            "block size and connector must match; TP compatibility is checked by the engine connector; "
            "differing fields: " + ", ".join(differences or ["field_types"])
        )
    return {key: value for key, value in params.items() if key != CONTRACT_KEY}
