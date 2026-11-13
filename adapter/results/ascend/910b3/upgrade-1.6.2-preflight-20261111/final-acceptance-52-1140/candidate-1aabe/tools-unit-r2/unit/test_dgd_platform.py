# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json

import pytest
from dynamo.profiler.utils.dgd_platform import adapt_vllm_blueprint_platform
from dynamo.vllm.kv_connector_protocols import ASCEND_MOONCAKE_MODULE

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


def worker(name, tp, role=None, connector=None):
    args = ["--tensor-parallel-size", str(tp)]
    if role:
        args += ["--disaggregation-mode", role]
    if connector is not None:
        args += ["--kv-transfer-config", json.dumps(connector)]
    return {
        "name": name,
        "type": "worker",
        "podTemplate": {
            "spec": {
                "containers": [
                    {
                        "name": "main",
                        "args": args,
                        "resources": {
                            "limits": {"nvidia.com/gpu": str(tp)},
                            "requests": {"nvidia.com/gpu": str(tp), "cpu": "2"},
                        },
                    }
                ]
            }
        },
    }


def override(names, platform="ascend"):
    return {
        "apiVersion": "nvidia.com/v1beta1",
        "spec": {
            "components": [
                {
                    "name": name,
                    "podTemplate": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "main",
                                    "env": [
                                        {"name": "DYN_PLATFORM", "value": platform}
                                    ],
                                }
                            ]
                        }
                    },
                }
                for name in names
            ]
        },
    }


def main(config, index=0):
    return config["spec"]["components"][index]["podTemplate"]["spec"]["containers"][0]


@pytest.mark.parametrize("tp", [1, 2, 8])
def test_ascend_candidate_maps_generated_device_count_and_removes_pd_default(tp):
    source = {
        "spec": {
            "components": [
                worker(
                    "W",
                    tp,
                    connector={"kv_connector": "NixlConnector", "kv_role": "kv_both"},
                )
            ]
        }
    }
    saved = copy.deepcopy(source)
    result = adapt_vllm_blueprint_platform(source, override(["W"]))
    container = main(result)
    assert container["resources"]["limits"] == {"huawei.com/Ascend910": str(tp)}
    assert container["resources"]["requests"] == {
        "huawei.com/Ascend910": str(tp),
        "cpu": "2",
    }
    assert "--kv-transfer-config" not in container["args"]
    assert source == saved


@pytest.mark.parametrize("tp_pair", [(1, 1), (2, 2), (1, 2), (4, 8), (8, 4), (2, 8)])
def test_ascend_pd_uses_both_peer_shapes_and_native_connector(tp_pair):
    source = {
        "spec": {
            "components": [
                worker("P", tp_pair[0], "prefill"),
                worker("D", tp_pair[1], "decode"),
            ]
        }
    }
    result = adapt_vllm_blueprint_platform(source, override(["P", "D"]))
    for index, role in enumerate(["kv_producer", "kv_consumer"]):
        args = main(result, index)["args"]
        kv = json.loads(args[args.index("--kv-transfer-config") + 1])
        assert kv["kv_role"] == role
        assert kv["kv_connector_module_path"] == ASCEND_MOONCAKE_MODULE
        extra = kv["kv_connector_extra_config"]
        assert extra["prefill"]["tp_size"] == tp_pair[0]
        assert extra["decode"]["tp_size"] == tp_pair[1]
        assert extra.get("heyi_tp_split", False) == (tp_pair == (1, 2))


@pytest.mark.parametrize("platform", ["cuda", "cpu"])
def test_other_platforms_preserve_defaults(platform):
    source = {"spec": {"components": [worker("W", 2)]}}
    assert adapt_vllm_blueprint_platform(source, override(["W"], platform)) == source


def test_custom_connector_is_preserved():
    custom = {
        "kv_connector": "DynamoConnector",
        "kv_role": "kv_both",
        "kv_connector_extra_config": {"custom": True},
    }
    source = {"spec": {"components": [worker("W", 1, connector=custom)]}}
    result = adapt_vllm_blueprint_platform(source, override(["W"]))
    assert main(result)["args"] == main(source)["args"]


def test_incomplete_pd_peer_contract_is_rejected():
    source = {"spec": {"components": [worker("P", 1, "prefill")]}}
    with pytest.raises(ValueError, match="both peer"):
        adapt_vllm_blueprint_platform(source, override(["P"]))


def test_frontend_and_unselected_worker_are_not_mapped():
    front = worker("F", 1)
    front["type"] = "frontend"
    source = {"spec": {"components": [front, worker("W", 1)]}}
    assert adapt_vllm_blueprint_platform(source, override(["F"])) == source


def test_metax_maps_resources_and_maca_transport():
    source = {
        "spec": {"components": [worker("P", 2, "prefill"), worker("D", 2, "decode")]}
    }
    result = adapt_vllm_blueprint_platform(source, override(["P", "D"], "metax"))
    for index in (0, 1):
        container = main(result, index)
        assert container["resources"]["limits"] == {"metax-tech.com/gpu": "2"}
        args = container["args"]
        kv = json.loads(args[args.index("--kv-transfer-config") + 1])
        assert kv["kv_connector_extra_config"]["mooncake_protocol"] == "maca"
        assert "kv_connector_module_path" not in kv


@pytest.mark.parametrize("role", ["kv_producer", "kv_consumer", "kv_both"])
def test_new_generated_nixl_roles_are_removed_for_standalone_vendor(role):
    source = {
        "spec": {
            "components": [
                worker(
                    "decode",
                    2,
                    connector={
                        "kv_connector": "NixlConnector",
                        "kv_role": role,
                    },
                )
            ]
        }
    }
    result = adapt_vllm_blueprint_platform(source, override(["decode"]))
    assert "--kv-transfer-config" not in main(result)["args"]
    assert main(result)["resources"]["limits"] == {"huawei.com/Ascend910": "2"}


def test_custom_nixl_options_are_not_rewritten():
    connector = {
        "kv_connector": "NixlConnector",
        "kv_role": "kv_producer",
        "kv_connector_extra_config": {"backends": ["UCX"]},
    }
    source = {"spec": {"components": [worker("decode", 2, connector=connector)]}}
    assert (
        main(adapt_vllm_blueprint_platform(source, override(["decode"])))["args"]
        == main(source)["args"]
    )


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("phase", ["prefill", "decode"])
def test_benchmark_override_selects_measured_role(legacy, phase):
    from dynamo.profiler.utils.dgd_platform import project_vllm_benchmark_override

    source = {"spec": {"components": [worker("decode", 2)]}}
    names = (
        ["VllmPrefillWorker", "VllmDecodeWorker"] if legacy else ["prefill", "decode"]
    )
    patch = override(["Frontend", *names, "typo"])
    for entry in patch["spec"]["components"]:
        entry["podTemplate"]["spec"]["nodeSelector"] = {"role": entry["name"]}
    original = copy.deepcopy(patch)
    result = project_vllm_benchmark_override(source, patch, phase)
    entries = result["spec"]["components"]
    assert [c["name"] for c in entries] == ["Frontend", "decode", "typo"]
    assert entries[1]["podTemplate"]["spec"]["nodeSelector"] == {
        "role": names[phase == "decode"]
    }
    assert patch == original  # The full override is still needed for final PD.


def test_decode_only_override_keeps_aggregate_prefill_compatibility():
    from dynamo.profiler.utils.dgd_platform import project_vllm_benchmark_override

    source = {"spec": {"components": [worker("worker", 1)]}}
    result = project_vllm_benchmark_override(
        source, override(["VllmDecodeWorker"]), "prefill"
    )
    assert result["spec"]["components"][0]["name"] == "worker"


def test_projection_rejects_duplicate_aliases_and_disaggregated_blueprints():
    from dynamo.profiler.utils.dgd_platform import project_vllm_benchmark_override

    source = {"spec": {"components": [worker("decode", 1)]}}
    with pytest.raises(ValueError, match="Duplicate"):
        project_vllm_benchmark_override(
            source, override(["prefill", "VllmPrefillWorker"]), "prefill"
        )
    source["spec"]["components"].append(worker("prefill", 1, "prefill"))
    with pytest.raises(ValueError, match="one aggregate"):
        project_vllm_benchmark_override(
            source, override(["prefill", "decode"]), "prefill"
        )
