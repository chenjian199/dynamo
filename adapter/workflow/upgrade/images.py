# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Observe already downloaded vendor images without pulling or touching host drivers."""

import json
import re
import subprocess

from adapter.workflow.core import now

PROBE = """import importlib.metadata as m,json,platform
names=['vllm','vllm-ascend','vllm-metax','torch','ai-dynamo','nixl','mooncake-transfer-engine']
v={}
for n in names:
 try:v[n]=m.version(n)
 except m.PackageNotFoundError:pass
print(json.dumps({'versions':v,'python':platform.python_version()}))"""


def invoke(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    if p.returncode:
        raise ValueError(p.stderr.strip() or "Image probe failed")
    return p.stdout


def probe(target, image, runtime="docker"):
    if runtime not in {"docker", "podman"} or image.startswith("-"):
        raise ValueError("Unsupported runtime or image reference")
    if image != target["image"]:
        raise ValueError(
            "Probe the planned image reference; revise the target plan before changing tags"
        )
    info = json.loads(invoke([runtime, "image", "inspect", image]))[0]
    if info["Architecture"] != target["architecture"]:
        raise ValueError("Image architecture differs from target")
    digests = info.get("RepoDigests", [])
    pinned = [
        d
        for d in digests
        if re.fullmatch(re.escape(target["repository"]) + r"@sha256:[a-f0-9]{64}", d)
    ]
    if not pinned:
        raise ValueError(
            "Need a RepoDigest from the planned official repository; load/save may lose it, preserve registry provenance"
        )
    # Use the inspected content ID, not the mutable tag, no devices/network or host mounts.
    observed = json.loads(
        invoke(
            [
                runtime,
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--entrypoint",
                target["python"],
                info["Id"],
                "-c",
                PROBE,
            ]
        )
    )
    for package in ["vllm"] + ([target["plugin"]] if target.get("plugin") else []):
        if observed["versions"].get(package, "").split("+")[0] != target["vllm"]:
            raise ValueError("Unexpected installed version of " + package)
    return dict(
        schema_version=1,
        observed_at=now(),
        source="local-image-inspect-and-metadata",
        image_id=info["Id"],
        repo_digest=min(pinned),
        architecture=info["Architecture"],
        requested_image=image,
        **observed,
    )


def validate_receipt(target, receipt):
    if receipt.get("source") != "local-image-inspect-and-metadata":
        raise ValueError("Receipt must come from probe-image, not a requested tag")
    if receipt.get("requested_image") != target["image"]:
        raise ValueError("Receipt belongs to a different planned image reference")
    if receipt.get("architecture") != target["architecture"]:
        raise ValueError("Receipt architecture mismatch")
    if not re.fullmatch(
        re.escape(target["repository"]) + r"@sha256:[a-f0-9]{64}",
        receipt.get("repo_digest", ""),
    ):
        raise ValueError("Receipt needs official repository digest")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", receipt.get("image_id", "")):
        raise ValueError("Receipt needs actual image ID")
    for package in ["vllm"] + ([target["plugin"]] if target.get("plugin") else []):
        if receipt.get("versions", {}).get(package, "").split("+")[0] != target["vllm"]:
            raise ValueError("Receipt version mismatch: " + package)
