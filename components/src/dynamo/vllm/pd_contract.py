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

CONTRACT_KEY = "_dynamo_pd_contract"


def build_pd_contract(config, platform_id):
    model = config.model_config
    hf_config = model.hf_config.to_dict()
    # Library metadata and mount locations do not describe the tensor layout.
    for key in ("_name_or_path", "transformers_version"):
        hf_config.pop(key, None)
    kv = config.kv_transfer_config
    parallel = config.parallel_config
    software = {"vllm": importlib.metadata.version("vllm")}
    if platform_id in ("metax", "ascend"):
        software["plugin"] = importlib.metadata.version("vllm-" + platform_id)
    return {
        "schema": 1,
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


def validate_pd_handoff(params, contract):
    if contract is None:
        return params
    if not isinstance(params, dict) or params.get(CONTRACT_KEY) != contract:
        raise InvalidArgument(
            "Incompatible PD peer: platform, engine version, model, precision, "
            "block size, parallelism and connector must match"
        )
    return {key: value for key, value in params.items() if key != CONTRACT_KEY}
