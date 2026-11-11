# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Docker platform-index normalization without relying on a daemon."""

import importlib.util
import json
from unittest.mock import patch

import pytest

from adapter.workflow.core import ROOT

spec = importlib.util.spec_from_file_location(
    "offline_image_helpers",
    ROOT / "adapter/modules/m10-validation/common/offline/image_helpers.py",
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def test_selected_platform_identity_is_preserved():
    def command(argv, **kwargs):
        if "--help" in argv:
            return "--platform"
        return json.dumps(
            [
                {
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Id": "platform-manifest"
                    if "--platform" in argv
                    else "multiarch-index",
                }
            ]
        ).encode()

    with patch.object(helpers.subprocess, "check_output", side_effect=command):
        images, platform = helpers.inspect_images(["image:tag"])
        assert images[0]["Id"] == "platform-manifest" and platform == "linux/amd64"
        assert helpers.save_command(["image:tag"], platform) == [
            "docker",
            "image",
            "save",
            "--platform",
            "linux/amd64",
            "image:tag",
        ]


def test_classic_engine_without_platform_option():
    def command(argv, **kwargs):
        if "--help" in argv:
            return "classic docker help"
        return json.dumps(
            [{"Os": "linux", "Architecture": "amd64", "Id": "config-id"}]
        ).encode()

    with patch.object(helpers.subprocess, "check_output", side_effect=command):
        assert helpers.inspect_images(["image:tag"])[0][0]["Id"] == "config-id"
        assert helpers.save_command(["image:tag"], "linux/amd64") == [
            "docker",
            "image",
            "save",
            "image:tag",
        ]


def test_mixed_architecture_bundle_rejected():
    output = json.dumps(
        [{"Os": "linux", "Architecture": arch} for arch in ["amd64", "arm64"]]
    ).encode()
    with (
        patch.object(helpers.subprocess, "check_output", return_value=output),
        pytest.raises(ValueError, match="one OS"),
    ):
        helpers.inspect_images(["image1", "image2"])


@pytest.fixture
def installed_image_audit():
    spec = importlib.util.spec_from_file_location(
        "installed_image_audit",
        ROOT / "adapter/modules/m10-validation/common/regression/check-installed.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installed_engine_matches_frozen_ascend_stack(installed_image_audit):
    profile = {
        "id": "ascend",
        "versions": {
            "vllm": "0.30.0+empty",
            "vllm-ascend": "0.30.0",
            "torch-npu": "2.10.0.post7",
        },
    }
    observed = {
        "vllm": "0.30.0+empty",
        "vllm-ascend": "0.30.0",
        "torch-npu": "2.10.0.post7",
    }
    installed_image_audit.validate_versions(profile, "ascend", observed.__getitem__)


@pytest.mark.parametrize("changed", ["vllm", "vllm-ascend", "torch-npu"])
def test_installed_engine_rejects_individual_vendor_drift(
    installed_image_audit, changed
):
    profile = {
        "id": "ascend",
        "versions": {
            "vllm": "0.30.0+empty",
            "vllm-ascend": "0.30.0",
            "torch-npu": "2.10.0.post7",
        },
    }
    observed = dict(profile["versions"], **{changed: "0.23.0"})
    with pytest.raises(AssertionError):
        installed_image_audit.validate_versions(profile, "ascend", observed.__getitem__)


def test_installed_engine_rejects_other_platform_lock(installed_image_audit):
    with pytest.raises(AssertionError):
        installed_image_audit.validate_versions(
            {"id": "cuda", "versions": {"vllm": "0.30.0"}},
            "ascend",
            lambda _: "0.30.0",
        )


def test_runtime_audit_retains_native_checks_without_optional_planner(
    installed_image_audit,
):
    modules = installed_image_audit.modules_for_role("runtime")
    assert "dynamo._core" in modules and "kvbm._core" in modules
    assert "dynamo.vllm" in modules and "gpu_memory_service" in modules
    assert "dynamo.planner" not in modules
    assert set(modules) < set(installed_image_audit.modules_for_role("full"))
    with pytest.raises(ValueError, match="Unknown image role"):
        installed_image_audit.modules_for_role("unknown")


def test_packaged_harness_contains_transitive_test_utilities(tmp_path):
    import ast
    import subprocess
    import sys

    deps = tmp_path / "deps"
    deps.mkdir()
    output = tmp_path / "harness"
    subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "adapter/modules/m10-validation/common/regression/prepare-harness.py"
            ),
            "--source",
            str(ROOT),
            "--output",
            str(output),
            "--test-deps",
            str(deps),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for path in output.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "tests.utils."
            ):
                target = output.joinpath(*node.module.split(".")).with_suffix(".py")
                assert target.is_file(), (path, node.module)
    assert not (output / "components/src").exists()
    assert not (output / "lib/bindings").exists()
