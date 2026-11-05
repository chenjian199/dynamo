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
