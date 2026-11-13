# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Derived from NVIDIA Dynamo. Modified by HEYI.

"""Integration coverage for the Python-to-Go DGD override protocol."""

from __future__ import annotations

import copy
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from dynamo.profiler.utils.dgd_override import apply_dgd_overrides

_RUN_INTEGRATION_ENV = "DYNAMO_RUN_DGD_OVERRIDE_GO_INTEGRATION"

pytestmark = [
    pytest.mark.pre_merge,
    pytest.mark.gpu_0,
    pytest.mark.integration,
    pytest.mark.planner,
    pytest.mark.timeout(360),
    pytest.mark.skipif(
        os.environ.get(_RUN_INTEGRATION_ENV) != "1",
        reason=f"set {_RUN_INTEGRATION_ENV}=1 to build and test the Go helper",
    ),
]


@pytest.fixture(scope="session")
def go_override_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    go = shutil.which("go")
    if go is None:
        pytest.fail("Go is required when the DGD override integration test is enabled")

    repository_root = Path(__file__).resolve().parents[6]
    operator_dir = repository_root / "deploy" / "operator"
    binary = tmp_path_factory.mktemp("dgd-override-go") / "dgd-apply-overrides"
    environment = os.environ.copy()
    environment.update({"CGO_ENABLED": "0", "GOWORK": "off"})

    try:
        result = subprocess.run(
            [
                go,
                "build",
                "-mod=readonly",
                "-trimpath",
                "-ldflags=-s -w",
                "-o",
                str(binary),
                "./cmd/dgd-apply-overrides",
            ],
            cwd=operator_dir,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("building dgd-apply-overrides timed out after 300 seconds")

    if result.returncode != 0:
        pytest.fail(
            "building dgd-apply-overrides failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    if not os.access(binary, os.X_OK):
        pytest.fail(f"Go build did not produce an executable at {binary}")
    return binary


def _named(items: list[dict], name: str) -> dict:
    matches = [item for item in items if item.get("name") == name]
    assert len(matches) == 1, f"expected one item named {name!r}, got {matches!r}"
    return matches[0]


def test_python_adapter_invokes_real_go_override_engine(
    go_override_binary: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    blueprint = {
        "apiVersion": "nvidia.com/v1beta1",
        "kind": "DynamoGraphDeployment",
        "metadata": {
            "name": "generated",
            "namespace": "default",
            "labels": {"base": "true"},
        },
        "spec": {
            "backendFramework": "vllm",
            "components": [
                {"name": "Frontend", "type": "frontend"},
                {
                    "name": "Worker",
                    "type": "worker",
                    "frontendSidecar": "sidecar",
                    "podTemplate": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "main",
                                    "image": "old-image",
                                    "args": ["--base"],
                                    "env": [
                                        {"name": "KEEP", "value": "keep"},
                                        {"name": "CHANGE", "value": "old"},
                                    ],
                                },
                                {"name": "sidecar", "image": "sidecar-image"},
                            ]
                        }
                    },
                },
            ],
        },
    }
    legacy_override = {
        "spec": {
            "services": {
                "Worker": {
                    "extraPodSpec": {
                        "mainContainer": {
                            "image": "new-image",
                            "args": ["--override"],
                            "env": [
                                {"name": "CHANGE", "value": "new"},
                                {"name": "ADDED", "value": "added"},
                            ],
                        }
                    }
                }
            }
        }
    }
    original_blueprint = copy.deepcopy(blueprint)
    original_override = copy.deepcopy(legacy_override)

    with caplog.at_level(logging.WARNING):
        effective = apply_dgd_overrides(
            blueprint,
            legacy_override,
            binary_path=str(go_override_binary),
        )

    assert effective["apiVersion"] == "nvidia.com/v1beta1"
    assert effective["kind"] == "DynamoGraphDeployment"
    assert effective["metadata"]["name"] == "generated"
    assert effective["metadata"]["namespace"] == "default"
    assert effective["metadata"]["labels"] == {"base": "true"}

    components = effective["spec"]["components"]
    assert _named(components, "Frontend")["type"] == "frontend"
    worker = _named(components, "Worker")
    assert worker["frontendSidecar"] == "sidecar"
    containers = worker["podTemplate"]["spec"]["containers"]
    assert _named(containers, "sidecar")["image"] == "sidecar-image"
    main = _named(containers, "main")
    assert main["image"] == "new-image"
    assert main["args"] == ["--base", "--override"]
    assert {entry["name"]: entry["value"] for entry in main["env"]} == {
        "KEEP": "keep",
        "CHANGE": "new",
        "ADDED": "added",
    }

    assert blueprint == original_blueprint
    assert legacy_override == original_override
    assert "treating it as nvidia.com/v1alpha1" in caplog.text


def _vendor_override(platform):
    return {
        "apiVersion": "nvidia.com/v1beta1",
        "kind": "DynamoGraphDeployment",
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
                                    "args": ["--enforce-eager"],
                                    "$patch": {"args": "append"},
                                }
                            ]
                        }
                    },
                }
                for name in ("VllmPrefillWorker", "VllmDecodeWorker")
            ]
        },
    }


@pytest.mark.parametrize(
    "platform,resource",
    [
        ("ascend", "huawei.com/Ascend910"),
        ("metax", "metax-tech.com/gpu"),
        ("cuda", "nvidia.com/gpu"),
    ],
)
def test_ais013_actual_candidates_materialize_with_real_go(
    go_override_binary,
    monkeypatch,
    platform,
    resource,
):
    """AISimulate 0.13 live capture: missing backend, canonical single-worker names."""
    import json

    from dynamo.profiler.utils.dgd_materialization import (
        DGDMaterializationPurpose,
        materialize_dgd,
    )
    from dynamo.profiler.utils.dgd_platform import project_vllm_benchmark_override

    monkeypatch.setenv("DYNAMO_DGD_APPLY_OVERRIDES_BIN", str(go_override_binary))
    path = Path(__file__).parents[1] / "fixtures/ais013_vllm_candidates.json"
    blueprints = json.loads(path.read_text())
    patch = _vendor_override(platform)
    saved = copy.deepcopy((blueprints, patch))
    for index, blueprint in enumerate(blueprints):
        phase = "prefill" if index < 2 else "decode"
        projected = project_vllm_benchmark_override(blueprint, patch, phase)
        result = materialize_dgd(
            blueprint,
            purpose=DGDMaterializationPurpose.BENCHMARK_CANDIDATE,
            override=projected,
            runtime_backend="vllm",
        )
        assert result["spec"]["backendFramework"] == "vllm"
        worker = _named(result["spec"]["components"], "decode")
        main = _named(worker["podTemplate"]["spec"]["containers"], "main")
        assert main["args"].count("--enforce-eager") == 1
        for field in ("limits", "requests"):
            assert main["resources"][field][resource] == str(1 + index % 2)
            if platform != "cuda":
                assert "nvidia.com/gpu" not in main["resources"][field]
        assert ("--kv-transfer-config" in main["args"]) == (platform == "cuda")
    assert (blueprints, patch) == saved


def test_vendor_final_pd_aliases_and_unknown_names_still_use_strict_go(
    go_override_binary,
    monkeypatch,
):
    import json

    from dynamo.profiler.utils.dgd_materialization import (
        DGDMaterializationPurpose,
        materialize_dgd,
    )
    from dynamo.profiler.utils.dgd_override import DGDOverrideError
    from dynamo.profiler.utils.dgd_template import load_dgd_template

    monkeypatch.setenv("DYNAMO_DGD_APPLY_OVERRIDES_BIN", str(go_override_binary))
    blueprint = load_dgd_template("vllm", "disagg")
    patch = _vendor_override("ascend")
    result = materialize_dgd(
        blueprint,
        purpose=DGDMaterializationPurpose.FINAL_OUTPUT,
        override=patch,
        runtime_backend="vllm",
    )
    for name, role in [("prefill", "kv_producer"), ("decode", "kv_consumer")]:
        component = _named(result["spec"]["components"], name)
        main = _named(component["podTemplate"]["spec"]["containers"], "main")
        args = main["args"]
        assert args.count("--enforce-eager") == 1
        kv = json.loads(args[args.index("--kv-transfer-config") + 1])
        assert kv["kv_connector"] == "MooncakeConnector"
        assert kv["kv_role"] == role
    patch["spec"]["components"].append({"name": "typo"})
    with pytest.raises(DGDOverrideError, match="typo"):
        materialize_dgd(
            blueprint,
            purpose=DGDMaterializationPurpose.FINAL_OUTPUT,
            override=patch,
            runtime_backend="vllm",
        )
