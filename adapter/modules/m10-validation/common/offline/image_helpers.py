# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Select one image platform without exporting unavailable multi-architecture descriptors."""

import json
import subprocess


def supports(command, flag):
    return flag in subprocess.check_output(
        ["docker", "image", command, "--help"], text=True
    )


def inspect_images(references, platform=None):
    images = json.loads(
        subprocess.check_output(["docker", "image", "inspect", *references])
    )
    platforms = {item["Os"] + "/" + item["Architecture"] for item in images}
    if len(platforms) != 1:
        raise ValueError("A platform bundle must contain one OS/architecture")
    selected = platform or next(iter(platforms))
    if supports("inspect", "--platform"):
        images = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", "--platform", selected, *references]
            )
        )
    if any(item["Os"] + "/" + item["Architecture"] != selected for item in images):
        raise ValueError("Requested image platform unavailable")
    return images, selected


def save_command(references, platform):
    args = ["docker", "image", "save"]
    if supports("save", "--platform"):
        args += ["--platform", platform]
    # Older classic-image Docker has no platform flag or multi-architecture index.
    # The archive verifier still rejects missing descriptor blobs on any backend.
    return args + list(references)
