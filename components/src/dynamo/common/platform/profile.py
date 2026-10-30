# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Versioned performance-profile identity; this is not a performance database."""

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProfileIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    platform: Literal["cuda", "ascend", "metax"]
    device_model: str = Field(min_length=1)
    driver: str = Field(min_length=1)
    sdk: str = Field(min_length=1)
    engine: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    plugin_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    precision: str = Field(min_length=1)
    parallelism: dict[str, int] = Field(min_length=1)
    kv_config: dict = Field(min_length=1)
    topology: dict = Field(min_length=1)
    runtime_image: str = Field(min_length=1)

    def fingerprint(self):
        return hashlib.sha256(
            json.dumps(
                self.model_dump(),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()


def write_profile_manifest(directory, identity, *, now=None):
    """Bind reusable bootstrap files to one explicitly supplied deployment identity."""
    identity = ProfileIdentity.model_validate(identity)
    directory = Path(directory)
    files = {}
    for name in (
        "prefill_raw_data.json",
        "decode_raw_data.json",
        "selected_prefill_interpolation/raw_data.npz",
        "selected_decode_interpolation/raw_data.npz",
    ):
        path = directory / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "planner-bootstrap",
        "identity": identity.model_dump(),
        "fingerprint": identity.fingerprint(),
        "created_at": time.time() if now is None else now,
        "files": files,
    }
    (directory / "profile-identity.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n"
    )
    return manifest


def validate_profile_manifest(
    directory, expected, *, max_age_seconds=7 * 86400, now=None
):
    """Reject cross-platform/version/topology reuse, stale data, and altered files."""
    expected = ProfileIdentity.model_validate(expected)
    now = time.time() if now is None else now
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("Profile maximum age must be positive and finite")
    directory = Path(directory)
    manifest = json.loads((directory / "profile-identity.json").read_text())
    if manifest.get("kind") != "planner-bootstrap":
        raise ValueError(
            "Profile contains benchmark summaries, not Planner FPM bootstrap data"
        )
    actual = ProfileIdentity.model_validate(manifest["identity"])
    if (
        manifest.get("schema_version") != 1
        or actual.fingerprint() != expected.fingerprint()
        or manifest.get("fingerprint") != actual.fingerprint()
    ):
        raise ValueError(
            "Performance profile identity mismatch; remeasure this deployment"
        )
    age = now - float(manifest["created_at"])
    if not math.isfinite(age) or age < -5 or age > max_age_seconds:
        raise ValueError("Performance profile expired or timestamp invalid")
    files = manifest["files"]
    if not files:
        raise ValueError("Performance profile has no measured bootstrap data")
    expected_files = {
        name
        for name in (
            "prefill_raw_data.json",
            "decode_raw_data.json",
            "selected_prefill_interpolation/raw_data.npz",
            "selected_decode_interpolation/raw_data.npz",
        )
        if (directory / name).is_file()
    }
    if set(files) != expected_files:
        raise ValueError("Performance profile data inventory changed")
    for name, digest in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Profile file escapes its directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Performance profile data changed: {name}")
    return manifest
