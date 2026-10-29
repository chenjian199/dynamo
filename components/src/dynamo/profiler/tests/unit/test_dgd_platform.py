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


@pytest.mark.parametrize("tp_pair", [(1, 1), (2, 2), (1, 2)])
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


@pytest.mark.parametrize("platform", ["cuda", "cpu", "metax"])
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
