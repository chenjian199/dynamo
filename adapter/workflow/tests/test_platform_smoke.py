# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The live M01 probe must reject version and exported-source drift."""

import hashlib
import importlib.util
import json

import pytest

from adapter.workflow.core import ROOT


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location(
        "platform_smoke",
        ROOT
        / "adapter/modules/m01-platform-contract/common/discovery/platform-smoke.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("platform", ["ascend", "metax"])
def test_current_lock_and_individual_package_drift(probe, platform):
    profile = json.loads((ROOT / f"container/platforms/{platform}.json").read_text())
    probe.validate_worker_stack(platform, profile, profile["versions"])
    for package in profile["versions"]:
        observed = dict(profile["versions"], **{package: "0.0.0+wrong-build"})
        with pytest.raises(AssertionError, match=package):
            probe.validate_worker_stack(platform, profile, observed)
    with pytest.raises(AssertionError, match="different platform"):
        probe.validate_worker_stack("unknown", profile, profile["versions"])


def test_exported_source_requires_exact_manifest_coverage(probe, tmp_path):
    source = tmp_path / "probe.py"
    source.write_text("original\n")
    hashes = {source.name: hashlib.sha256(source.read_bytes()).hexdigest()}
    manifest = {"commit": "a" * 40, "files": hashes}
    (tmp_path / "SOURCE.json").write_text(json.dumps(manifest))
    assert probe.source_identity(tmp_path, [source.name]) == ("a" * 40, hashes)
    source.write_text("modified\n")
    with pytest.raises(AssertionError, match="manifest mismatch"):
        probe.source_identity(tmp_path, [source.name])
    source.write_text("original\n")
    manifest["files"] = {}
    (tmp_path / "SOURCE.json").write_text(json.dumps(manifest))
    with pytest.raises(AssertionError, match="manifest mismatch"):
        probe.source_identity(tmp_path, [source.name])
