# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise actual module paths after the adapter directory migration."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.gpu_0,
    pytest.mark.pre_merge,
    pytest.mark.timeout(30),
]


@pytest.mark.parametrize("platform", ["cuda", "ascend"])
def test_release_renderer_resolves_module_sources(platform, tmp_path):
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("render.py")),
            "--platform",
            platform,
            "--namespace",
            "release-test",
            "--runtime-image",
            "example.invalid/runtime:test",
            "--runtime-version",
            "1.6.2",
            "--tools-image",
            "example.invalid/tools:test",
            "--workdir",
            "/release-test",
            "--output",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=25,
    )
    for name in [
        "aggregate",
        "pd",
        "pd-cross",
        "grove",
        "grove-kai",
        "planner-small",
        "planner-sla",
        "planner-qwen32",
        "planner-deepseek",
        "pd-rdma",
    ]:
        obj = json.loads((tmp_path / (name + ".json")).read_text())
        assert obj["metadata"]["namespace"] == "release-test"
        for component in obj["spec"]["components"]:
            assert component["runtimeVersionOverride"] == "1.6.2"
            pod = component["podTemplate"]["spec"]
            assert not {"m09-source", "m09-deps"} & {
                v["name"] for v in pod.get("volumes", [])
            }
            for container in pod["containers"]:
                expected = "tools" if component["type"] == "planner" else "runtime"
                assert container["image"] == f"example.invalid/{expected}:test"
                assert "PYTHONPATH" not in {e["name"] for e in container.get("env", [])}
