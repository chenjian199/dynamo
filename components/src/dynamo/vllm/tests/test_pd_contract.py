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
    assert original["tp_reshard"] is None  # Preserve the legacy wire field.
    hf["_name_or_path"] = "/other"
    assert build_pd_contract(cfg, "ascend") == original
    hf["hidden_size"] = 256
    assert (
        build_pd_contract(cfg, "ascend")["config_sha256"] != original["config_sha256"]
    )
    direct = build_pd_contract(cfg, "ascend")
    transport = dict(vars(cfg.kv_transfer_config))
    cfg.kv_transfer_config = SimpleNamespace(
        kv_connector="PdConnector",
        kv_connector_extra_config={
            "connectors": [{"kv_connector": "DynamoConnector"}, transport]
        },
    )
    assert build_pd_contract(cfg, "ascend") == direct
    transport["kv_connector_module_path"] = "unknown.module"
    with pytest.raises(ValueError, match="Unsupported PD connector module"):
        build_pd_contract(cfg, "ascend")


@pytest.mark.parametrize("platform", ["cuda", "metax", "metax-nixl", "ascend"])
def test_pd_preflight_checks_roles_and_parallelism(platform, monkeypatch):
    from dynamo.common.platform import providers

    monkeypatch.setattr(providers, "validate_vllm_versions", lambda platform: {})
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
    valid_role = kv.kv_role
    kv.kv_role = "invalid"
    with pytest.raises(PlatformError):
        validate_pd_configuration(cfg, platform)
    kv.kv_role = valid_role
    cfg.engine_args.tensor_parallel_size = 2
    if platform == "ascend":
        with pytest.raises(PlatformError, match="peer parallel"):
            validate_pd_configuration(cfg, platform)
        kv.kv_connector_extra_config["prefill"]["tp_size"] = 2
    assert validate_pd_configuration(cfg, platform) == kv.kv_connector
    for tp in (4, 8):
        cfg.engine_args.tensor_parallel_size = tp
        cfg.engine_args.quantization = "ascend" if platform == "ascend" else "fp8"
        cfg.engine_args.enable_expert_parallel = True
        if platform == "ascend":
            for role in ("prefill", "decode"):
                kv.kv_connector_extra_config[role]["tp_size"] = tp
        assert validate_pd_configuration(cfg, platform) == kv.kv_connector
        caps = providers.build_capabilities(
            platform,
            {
                "engine": "vllm",
                "task": "text",
                "eager": "true",
                "tp": str(tp),
                "dp": "1",
                "pd_connector": kv.kv_connector,
            },
            {},
        )
        assert caps["pd.transfer"]["state"] == "available"
    if platform == "ascend":
        kv.kv_connector_extra_config["decode"]["tp_size"] = 4
        assert validate_pd_configuration(cfg, platform) == kv.kv_connector
    cfg.engine_args.tensor_parallel_size = 0
    with pytest.raises(PlatformError, match="positive TP"):
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


@pytest.mark.parametrize(
    "platform,connector,module",
    [
        ("cuda", "NixlConnector", None),
        ("metax", "NixlConnector", None),
        ("metax", "MooncakeConnector", None),
        ("ascend", "MooncakeConnector", ASCEND_MOONCAKE_MODULE),
    ],
)
@pytest.mark.parametrize(
    "ptp,dtp", [(1, 2), (2, 1), (2, 4), (4, 2), (4, 8), (8, 4), (1, 8), (8, 1), (3, 5)]
)
def test_heterogeneous_tp_delegates_layout_to_connector(
    platform, connector, module, ptp, dtp
):
    local = dict(
        schema=2,
        platform=platform,
        connector=connector,
        connector_module=module,
        tp=dtp,
        dp=1,
        pp=1,
        model="qwen",
        dtype="bf16",
        block_size=64,
    )
    peer = {**local, "tp": ptp}
    metadata = {"remote_block_ids": [[3]], CONTRACT_KEY: peer}
    assert validate_pd_handoff(metadata, local) == {"remote_block_ids": [[3]]}
    for field, value in [
        ("tp", 0),
        ("tp", -1),
        ("tp", "4"),
        ("tp", 4.0),
        ("tp", True),
        ("schema", 1),
        ("dtype", "fp8"),
        ("block_size", 128),
        ("config_sha256", "other-model-layout"),
        ("software", {"vllm": "other-version"}),
        ("dp", 2),
        ("pp", 2),
        ("connector_module", "unknown"),
    ]:
        with pytest.raises(InvalidArgument):
            validate_pd_handoff(
                {**metadata, CONTRACT_KEY: {**peer, field: value}}, local
            )
    assert peer["tp"] == ptp

    # A stale certification hint cannot forbid native MLA, replicated-head, or
    # other engine-supported layouts. It is not sent to the native connector.
    for hint in (None, "mha-gqa"):
        assert validate_pd_handoff(
            {**metadata, CONTRACT_KEY: {**peer, "tp_reshard": hint}}, local
        ) == {"remote_block_ids": [[3]]}
        assert validate_pd_handoff(metadata, {**local, "tp_reshard": hint}) == {
            "remote_block_ids": [[3]]
        }


@pytest.mark.parametrize("tp", [0, -1, True, "4", 4.0, None])
def test_equal_tp_contract_rejects_invalid_sizes(tp):
    contract = {"schema": 2, "tp": tp}
    with pytest.raises(InvalidArgument):
        validate_pd_handoff({CONTRACT_KEY: dict(contract)}, contract)


@pytest.mark.parametrize(
    "pair", [(1, 2), (2, 1), (4, 8), (8, 4), (2, 8), (8, 2), (3, 5)]
)
@pytest.mark.parametrize(
    "role", [DisaggregationMode.PREFILL, DisaggregationMode.DECODE]
)
def test_ascend_native_tp_pairs_pass_through_without_split_extension(pair, role):
    extra = {
        "prefill": {"tp_size": pair[0], "dp_size": 1, "pp_size": 1},
        "decode": {"tp_size": pair[1], "dp_size": 1, "pp_size": 1},
    }
    is_prefill = role == DisaggregationMode.PREFILL
    kv = config().kv_transfer_config
    kv.kv_role = "kv_producer" if is_prefill else "kv_consumer"
    kv.kv_connector_extra_config = extra
    cfg = SimpleNamespace(
        disaggregation_mode=role,
        enable_multimodal=False,
        engine_args=SimpleNamespace(
            kv_transfer_config=kv,
            tensor_parallel_size=pair[0] if is_prefill else pair[1],
            data_parallel_size=1,
            enforce_eager=True,
            enable_lora=False,
            speculative_config=None,
        ),
    )
    assert validate_pd_configuration(cfg, "ascend") == "MooncakeConnector"
    assert "heyi_tp_split" not in extra
    # The separate HEYI extension has actual two-rank address/ACK assumptions;
    # these restrictions do not apply to the native connector path above.
    extra["heyi_tp_split"] = True
    if pair == (1, 2):
        assert validate_pd_configuration(cfg, "ascend") == "MooncakeConnector"
    else:
        with pytest.raises(PlatformError, match="peer parallel"):
            validate_pd_configuration(cfg, "ascend")


def test_ascend_mooncake_libraries_are_loaded_globally_once(monkeypatch):
    import ctypes

    from dynamo.common.platform import ascend_mooncake

    calls = []
    marker = object()

    def load(path, mode):
        calls.append((path, mode))
        return marker

    monkeypatch.setattr(ascend_mooncake, "_cann_datadist", None)
    monkeypatch.setattr(ascend_mooncake.ctypes, "CDLL", load)
    ascend_mooncake.prepare_mooncake_libraries()
    ascend_mooncake.prepare_mooncake_libraries()
    assert calls == [
        ("/usr/local/Ascend/cann-9.1.0/lib64/libllm_datadist.so", ctypes.RTLD_GLOBAL)
    ]
    assert ascend_mooncake._cann_datadist is marker


@pytest.mark.parametrize("platform", ["cuda", "metax", "ascend"])
def test_kvbm_pd_preflight_validates_transport_child_and_prefill_only(platform):
    from dynamo.common.platform.preflight import validate_kvbm_configuration

    transport = dict(kv_connector="NixlConnector", kv_role="kv_both")
    if platform != "cuda":
        transport = dict(
            kv_connector="MooncakeConnector",
            kv_role="kv_producer",
            kv_connector_extra_config={"mooncake_protocol": "maca"},
        )
    if platform == "ascend":
        transport.update(
            kv_connector_module_path=ASCEND_MOONCAKE_MODULE,
            kv_connector_extra_config={
                "prefill": {"tp_size": 1, "dp_size": 1},
                "decode": {"tp_size": 1, "dp_size": 1},
            },
        )
    offload = dict(
        kv_connector="DynamoConnector",
        kv_role="kv_both",
        kv_connector_module_path="kvbm.vllm_integration.connector",
    )
    wrapper = SimpleNamespace(
        kv_connector="PdConnector",
        kv_role="kv_both",
        kv_connector_module_path="kvbm.vllm_integration.connector",
        kv_connector_extra_config={"connectors": [offload, transport]},
    )
    cfg = SimpleNamespace(
        disaggregation_mode=DisaggregationMode.PREFILL,
        enable_multimodal=False,
        engine_args=SimpleNamespace(
            kv_transfer_config=wrapper,
            tensor_parallel_size=1,
            data_parallel_size=1,
            enforce_eager=True,
            enable_lora=False,
            quantization=None,
            speculative_config=None,
            enable_expert_parallel=False,
        ),
    )
    validate_kvbm_configuration(cfg, platform, SimpleNamespace(**offload))
    assert validate_pd_configuration(cfg, platform) == transport["kv_connector"]
    offload["kv_connector_module_path"] = "unknown.module"
    with pytest.raises(PlatformError, match="KVBM requires"):
        validate_pd_configuration(cfg, platform)
    offload["kv_connector_module_path"] = "kvbm.vllm_integration.connector"
    cfg.disaggregation_mode = DisaggregationMode.DECODE
    with pytest.raises(PlatformError, match="on prefill"):
        validate_pd_configuration(cfg, platform)
    cfg.disaggregation_mode = DisaggregationMode.PREFILL
    transport["kv_role"] = "invalid"
    with pytest.raises(PlatformError, match="peer parallel"):
        validate_pd_configuration(cfg, platform)
