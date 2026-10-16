# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
from types import SimpleNamespace

import pytest

from dynamo.common.constants import DisaggregationMode
from dynamo.common.platform.preflight import (
    configure_pd_environment,
    validate_pd_configuration,
)
from dynamo.common.platform.registry import PlatformError
from dynamo.llm.exceptions import InvalidArgument
from dynamo.vllm.kv_connector_protocols import (
    ASCEND_MOONCAKE_MODULE,
    AscendMooncakeConnectorProtocol,
    make_kv_connector_protocol,
)
from dynamo.vllm.pd_contract import (
    CONTRACT_KEY,
    attach_pd_contract,
    build_pd_contract,
    validate_pd_handoff,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.vllm,
    pytest.mark.gpu_0,
    pytest.mark.pre_merge,
]


def config(module=ASCEND_MOONCAKE_MODULE):
    return SimpleNamespace(
        kv_transfer_config=SimpleNamespace(
            kv_connector="MooncakeConnector", kv_connector_module_path=module
        )
    )


def test_vendor_mooncake_is_not_upstream_bootstrap_protocol():
    protocol = make_kv_connector_protocol(config())
    assert isinstance(protocol, AscendMooncakeConnectorProtocol)
    assert "transfer_id" not in protocol.prefill_request_kv_transfer_params()
    payload = dict(
        do_remote_prefill=True,
        remote_engine_id="p",
        remote_request_id="r",
        remote_block_ids=[[1, 2]],
        remote_host="10.0.0.1",
        remote_port=1000,
        remote_block_size=128,
        remote_pcp_size=1,
        last_token_id=42,
    )
    assert (
        protocol.decode_request_kv_transfer_params(
            SimpleNamespace(kv_transfer_params=payload)
        )
        is payload
    )
    with pytest.raises(ValueError, match="required KV"):
        protocol.decode_request_kv_transfer_params(
            SimpleNamespace(kv_transfer_params=None)
        )


def test_same_class_name_does_not_authorize_unknown_module():
    with pytest.raises(ValueError, match="Unsupported PD connector module"):
        make_kv_connector_protocol(config("unknown.mooncake"))


def test_wrapper_uses_child_module():
    cfg = config("kvbm.vllm_integration.connector")
    cfg.kv_transfer_config.kv_connector = "PdConnector"
    cfg.kv_transfer_config.kv_connector_extra_config = {
        "connectors": [
            {
                "kv_connector": "MooncakeConnector",
                "kv_connector_module_path": ASCEND_MOONCAKE_MODULE,
            }
        ]
    }
    assert isinstance(make_kv_connector_protocol(cfg), AscendMooncakeConnectorProtocol)
    cfg.kv_transfer_config.kv_connector_extra_config["connectors"] = [
        {"kv_connector": "NixlConnector"}
    ]
    assert type(make_kv_connector_protocol(cfg)).__name__ == "NixlConnectorProtocol"


@pytest.mark.parametrize(
    "field",
    [
        "platform",
        "software",
        "model",
        "revision",
        "config_sha256",
        "dtype",
        "cache_dtype",
        "block_size",
        "tp",
        "dp",
        "pp",
        "connector",
        "connector_module",
        "layout_override",
    ],
)
def test_peer_mismatch_rejected_before_engine_submission(field):
    contract = {field: "expected"}
    params = {"remote_block_ids": [[1]], CONTRACT_KEY: {field: "wrong"}}
    with pytest.raises(InvalidArgument, match="Incompatible PD peer"):
        validate_pd_handoff(params, contract)


def test_contract_preserves_vendor_metadata_without_mutation():
    contract = {"schema": 1}
    params = {"remote_request_id": "r", "remote_block_ids": [[1]], "last_token_id": 42}
    handoff = attach_pd_contract(params, contract)
    assert validate_pd_handoff(handoff, contract) == params
    assert CONTRACT_KEY not in params and CONTRACT_KEY in handoff
    assert validate_pd_handoff(None, None) is None  # Legacy behavior.
    with pytest.raises(InvalidArgument):
        validate_pd_handoff(params, contract)
    with pytest.raises(InvalidArgument):
        attach_pd_contract(None, contract)


def test_contract_uses_effective_model_configuration(monkeypatch):
    monkeypatch.setattr("importlib.metadata.version", lambda name: "test-version")
    hf = {
        "hidden_size": 128,
        "_name_or_path": "/temporary",
        "transformers_version": "99",
    }
    cfg = config()
    cfg.model_config = SimpleNamespace(
        model="weights",
        revision=None,
        dtype="bf16",
        hf_config=SimpleNamespace(to_dict=lambda: dict(hf)),
    )
    cfg.parallel_config = SimpleNamespace(
        tensor_parallel_size=1, data_parallel_size=1, pipeline_parallel_size=1
    )
    cfg.cache_config = SimpleNamespace(cache_dtype="auto", block_size=128)
    original = build_pd_contract(cfg, "ascend")
    hf["_name_or_path"] = "/other"
    assert build_pd_contract(cfg, "ascend") == original
    hf["hidden_size"] = 256
    assert (
        build_pd_contract(cfg, "ascend")["config_sha256"] != original["config_sha256"]
    )


@pytest.mark.parametrize("platform", ["cuda", "metax", "metax-nixl", "ascend"])
def test_pd_preflight_checks_roles_and_parallelism(platform):
    cfg = SimpleNamespace(
        disaggregation_mode=DisaggregationMode.PREFILL, enable_multimodal=False
    )
    kv = config().kv_transfer_config
    kv.kv_role = "kv_producer"
    kv.kv_connector_extra_config = {
        "prefill": {"tp_size": 1, "dp_size": 1},
        "decode": {"tp_size": 1, "dp_size": 1},
    }
    if platform == "metax":
        kv.kv_connector_module_path = None
        kv.kv_connector_extra_config = {"mooncake_protocol": "maca"}
    if platform in ("cuda", "metax-nixl"):
        kv.kv_connector, kv.kv_connector_module_path, kv.kv_role = (
            "NixlConnector",
            None,
            "kv_both",
        )
    platform = platform.removesuffix("-nixl")
    cfg.engine_args = SimpleNamespace(
        kv_transfer_config=kv,
        tensor_parallel_size=1,
        data_parallel_size=1,
        enforce_eager=True,
        enable_lora=False,
        quantization=None,
        speculative_config=None,
        enable_expert_parallel=False,
    )
    assert validate_pd_configuration(cfg, platform) == kv.kv_connector
    kv.kv_role = "invalid"
    with pytest.raises(PlatformError):
        validate_pd_configuration(cfg, platform)
    cfg.engine_args.tensor_parallel_size = 2
    with pytest.raises(PlatformError, match="TP1/DP1"):
        validate_pd_configuration(cfg, platform)


def test_metax_nixl_requires_device_aware_protocols(monkeypatch):
    monkeypatch.delenv("UCX_PROTO_ENABLE", raising=False)
    monkeypatch.delenv("UCX_TLS", raising=False)
    configure_pd_environment("cuda", "NixlConnector")
    configure_pd_environment("metax", "MooncakeConnector")
    assert "UCX_PROTO_ENABLE" not in os.environ
    configure_pd_environment("metax", "NixlConnector")
    assert os.environ["UCX_PROTO_ENABLE"] == "y"
    assert os.environ["UCX_TLS"] == "maca_ipc,maca_copy,tcp"
    monkeypatch.setenv("UCX_TLS", "maca_copy,tcp")
    configure_pd_environment("metax", "NixlConnector")
    assert os.environ["UCX_TLS"] == "maca_copy,tcp"
    monkeypatch.setenv("UCX_PROTO_ENABLE", "n")
    with pytest.raises(PlatformError, match="unsafe GPU GET"):
        configure_pd_environment("metax", "NixlConnector")
