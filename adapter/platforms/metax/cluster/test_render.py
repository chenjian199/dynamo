# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CPU-only contracts; these do not certify a MetaX cluster or transfer link."""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from dynamo.common.platform.profile import ProfileIdentity
from dynamo.profiler.utils.dgdr_v1beta1_types import DynamoGraphDeploymentRequestSpec
from dynamo.profiler.utils.platform_profile import (
    dgdr_platform,
    require_system_capacity,
)
from dynamo.profiler.utils.dgd_materialization import (
    materialize_dgd,
    DGDMaterializationPurpose,
)

spec = importlib.util.spec_from_file_location(
    "metax_cluster", Path(__file__).with_name("render.py")
)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
pytestmark = [pytest.mark.unit, pytest.mark.gpu_0]


@pytest.fixture
def config():
    return {
        "name": "metax-check",
        "namespace": "test-metax",
        "runtime_image": "test/runtime:metax",
        "tools_image": "test/tools:metax",
        "prefill_node": "metax-a",
        "decode_node": "metax-b",
        "model": "/models/Qwen3-0.6B",
        "model_host_path": "/test-models",
        "devices_per_node": 8,
        "vram_mb": 65536,
        "gpu_budget": 8,
    }


def test_cross_node_pd_and_cpu_isolation(config):
    config.update(prefill_replicas=2, decode_replicas=3)
    result = renderer.render(config)
    front, prefill, decode = result["pd-cross.json"]["spec"]["components"]
    assert [prefill["replicas"], decode["replicas"]] == [2, 3]
    assert renderer.pod(prefill)["nodeSelector"]["kubernetes.io/hostname"] == "metax-a"
    assert renderer.pod(decode)["nodeSelector"]["kubernetes.io/hostname"] == "metax-b"
    for comp in (prefill, decode):
        assert "affinity" not in renderer.pod(comp)
        c = renderer.main_container(comp)
        kv = json.loads(c["args"][c["args"].index("--kv-transfer-config") + 1])
        assert kv["kv_connector_extra_config"]["mooncake_protocol"] == "maca"
        assert c["resources"]["limits"][renderer.RESOURCE] == 1
    cpu = renderer.main_container(front)
    assert renderer.RESOURCE not in cpu["resources"]["limits"]
    assert {"name": "DYN_PLATFORM", "value": "cpu"} in cpu["env"]
    planner = result["planner-load.json"]["spec"]["components"][-1]
    assert (
        renderer.RESOURCE not in renderer.main_container(planner)["resources"]["limits"]
    )
    assert (
        result["grove.json"]["metadata"]["annotations"]["nvidia.com/enable-grove"]
        == "true"
    )


@pytest.mark.parametrize("pair", [(1, 2), (2, 4), (4, 8), (8, 4), (3, 5)])
def test_native_tp_not_limited_to_tested_pairs(config, pair):
    config.update(prefill_tp=pair[0], decode_tp=pair[1])
    result = renderer.render(config)
    for comp, tp in zip(result["pd-cross.json"]["spec"]["components"][1:], pair):
        c = renderer.main_container(comp)
        assert c["args"][c["args"].index("--tensor-parallel-size") + 1] == str(tp)
        assert c["resources"]["limits"][renderer.RESOURCE] == tp


@pytest.mark.parametrize(
    "change,match",
    [
        ({"decode_node": "metax-a"}, "different"),
        ({"prefill_tp": 0}, "positive"),
        ({"prefill_tp": 9}, "distributed executor"),
        ({"prefill_tp": 4, "prefill_replicas": 3}, "capacity"),
        ({"model_pvc": "models"}, "exactly one"),
        ({"planner_files": True}, "identity"),
        ({"network_env": {"DYN_PLATFORM": "cuda"}}, "transport"),
    ],
)
def test_invalid_inputs_fail_before_render(config, change, match):
    config.update(change)
    with pytest.raises(ValueError, match=match):
        renderer.render(config)


def test_profiler_contract_uses_actual_metax_capacity(config, tmp_path):
    result = renderer.render(config)
    dgdr = DynamoGraphDeploymentRequestSpec.model_validate(result["dgdr.json"]["spec"])
    assert dgdr.hardware.gpuSku.value == "metax_c500"
    assert dgdr_platform(dgdr) == "metax"
    assert dgdr.searchStrategy.value == "thorough"
    assert dgdr.autoApply is False
    (tmp_path / "metax_c500.yaml").write_text(
        result["profile-inputs.json"]["data"]["metax_c500.yaml"]
    )
    capacity = require_system_capacity("metax_c500", [tmp_path], config["vram_mb"])
    assert capacity["capacity_bytes"] == config["vram_mb"] * 2**20
    assert capacity["performance_database_certified"] is False
    for comp in dgdr.overrides.dgd["spec"]["components"]:
        assert "args" not in renderer.main_container(comp)
        assert (
            renderer.RESOURCE
            not in renderer.main_container(comp)["resources"]["limits"]
        )


def test_actual_override_cli_preserves_candidate_tp(config):
    result = renderer.render(config)
    # A generated CUDA-shaped candidate must be mapped before the Go override
    # merge, while candidate TP and peer shapes remain authoritative.
    source = copy.deepcopy(result["pd-cross.json"])
    for comp, tp in zip(source["spec"]["components"][1:], (4, 8)):
        c = renderer.main_container(comp)
        c["args"] = c["args"][3:]
        c["args"][c["args"].index("--tensor-parallel-size") + 1] = str(tp)
        c["args"][c["args"].index("--kv-transfer-config") + 1] = json.dumps(
            {"kv_connector": "NixlConnector", "kv_role": "kv_both"}
        )
        c["resources"] = {
            "requests": {"nvidia.com/gpu": tp},
            "limits": {"nvidia.com/gpu": tp},
        }
    actual = materialize_dgd(
        source,
        purpose=DGDMaterializationPurpose.BENCHMARK_CANDIDATE,
        override=result["dgdr.json"]["spec"]["overrides"]["dgd"],
        runtime_backend="vllm",
    )
    for comp, tp in zip(actual["spec"]["components"][1:], (4, 8)):
        c = renderer.main_container(comp)
        assert c["resources"]["limits"][renderer.RESOURCE] == tp
        assert "nvidia.com/gpu" not in c["resources"]["limits"]
        kv = json.loads(c["args"][c["args"].index("--kv-transfer-config") + 1])
        assert kv["kv_connector_extra_config"]["prefill"]["tp_size"] == 4
        assert kv["kv_connector_extra_config"]["decode"]["tp_size"] == 8
        assert kv["kv_connector_extra_config"]["mooncake_protocol"] == "maca"


def test_file_bootstrap_is_explicit_and_cpu_only(config):
    identity = ProfileIdentity(
        platform="metax",
        device_model="test C500",
        driver="test-driver",
        sdk="test-sdk",
        engine="vllm",
        engine_version="0.26.0",
        plugin_version="test-plugin",
        model=config["model"],
        model_revision="test-config-hash",
        precision="bf16",
        parallelism={"tp": 1},
        kv_config={"block_size": 128},
        topology={"nodes": 2},
        runtime_image="test-digest",
    )
    config.update(
        planner_files=True,
        identity=identity.model_dump(),
        prometheus_endpoint="http://prometheus.test:9090",
    )
    output = renderer.render(config)
    dgdr = DynamoGraphDeploymentRequestSpec.model_validate(output["dgdr.json"]["spec"])
    assert dgdr.features.planner.pre_deployment_metrics_source == "files"
    assert dgdr.features.planner.target_platform == "metax"
    pc = renderer.main_container(dgdr.overrides.dgd["spec"]["components"][-1])
    assert "args" not in pc  # Generated --config and its ConfigMap mount survive.
    assert {"name": "DYN_PLATFORM", "value": "cpu"} in pc["env"]


def test_identity_cli_records_metax_plugin_and_requires_inventory(config, tmp_path):
    script = renderer.ROOT / "adapter/m09/profile_identity.py"
    provenance = tmp_path / "provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "platform": "metax",
                "driver": "test-driver",
                "sdk": "test-sdk",
                "packages": {"vllm": "0.26.0", "vllm-metax": "test-metax-plugin"},
                "model": config["model"],
                "model_config_sha256": "test-sha",
                "node": "test-node",
                "image_id": "test-image",
            }
        )
    )
    output = tmp_path / "identity.json"
    command = [
        sys.executable,
        str(script),
        "--provenance",
        str(provenance),
        "--device-model",
        "test C500",
        "--output",
        str(output),
    ]
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "--devices-per-node" in failed.stderr
    subprocess.run(command + ["--devices-per-node", "4", "--nodes", "2"], check=True)
    identity = ProfileIdentity.model_validate_json(output.read_text())
    assert identity.plugin_version == "test-metax-plugin"
    assert identity.topology["devices_per_node"] == 4
    assert identity.topology["nodes"] == 2
