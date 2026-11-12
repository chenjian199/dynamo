# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise real packaging copies with Docker replaced; no image build or GPU use."""

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
TRANSFER = Path("adapter/modules/m05-pd-transfer/common/transfer")
CACHE = Path("adapter/modules/m06-kvbm-collectives/common/cache")
IMAGES = Path("adapter/modules/m10-validation/common/images")

pytestmark = pytest.mark.unit


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_m02_stages_complete_harness(tmp_path):
    """Run real shell copies, then stop at the Docker upload boundary."""
    capture = tmp_path / "captured harness"
    shell_env = tmp_path / "docker-stub.sh"
    shell_env.write_text(
        """docker() {
    case "$1" in
        exec)
            [[ "$3" == test && "$4" == '!' && "$5" == -e && "$6" == /test ]]
            ;;
        cp)
            [[ "$3" == fixture-container:/test ]] || exit 74
            cp -a "$2" "$M02_CAPTURE"
            printf '%s' "$2" > "$M02_CAPTURE.source"
            exit 73
            ;;
        *) exit 74 ;;
    esac
}
"""
    )
    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "adapter/modules/m02-build-packaging/common/images/run.sh"),
            "ascend",
            "fixture-container",
            "0,1",
            str(tmp_path / "build"),
            str(tmp_path / "output"),
        ],
        cwd=tmp_path,
        env=dict(os.environ, BASH_ENV=str(shell_env), M02_CAPTURE=str(capture)),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 73, completed.stdout + completed.stderr
    # M05 and M06 resolve imports/sibling assets relative to this package layout.
    for relative in (
        TRANSFER / "run.py",
        TRANSFER / "patch_vendor.py",
        TRANSFER / "patch_ascend_tp.py",
        CACHE / "run.py",
        CACHE / "sources.txt",
        Path("tests/__init__.py"),
        Path("tests/utils/managed_process.py"),
        Path("tests/utils/port_utils.py"),
    ):
        assert (capture / relative).read_bytes() == (ROOT / relative).read_bytes()
    for source in (ROOT / TRANSFER / "patches").iterdir():
        if source.is_file():
            assert (
                capture / TRANSFER / "patches" / source.name
            ).read_bytes() == source.read_bytes()
    for name in ("smoke.py", "native-smoke.py", "cpu-smoke.py"):
        assert (capture / name).read_bytes() == (
            ROOT / "container/platforms" / name
        ).read_bytes()
    assert (capture / "gms/test_platform_vmm.py").is_file()
    assert not Path(Path(str(capture) + ".source").read_text()).exists()


@pytest.mark.parametrize("refresh", [False, True])
def test_m10_rebuild_vendor_patch_context(tmp_path, monkeypatch, refresh):
    """Build the real context and checksum manifest, mocking only Docker calls."""
    source = tmp_path / "source checkout"
    transfer = source / TRANSFER
    transfer.mkdir(parents=True)
    for name in ("patch_vendor.py", "patch_ascend_tp.py"):
        shutil.copy2(ROOT / TRANSFER / name, transfer / name)
    shutil.copytree(ROOT / TRANSFER / "patches", transfer / "patches")
    original = tmp_path / "original"
    context = original / "image-context"
    artifacts = context / "artifacts"
    (artifacts / "wheels").mkdir(parents=True)
    (artifacts / "vendor-patches/patches").mkdir(parents=True)
    for name in ("patch_vendor.py", "patch_ascend_tp.py"):
        (artifacts / "vendor-patches" / name).write_text("# old installer\n")
    (artifacts / "vendor-patches/patches/ascend-tp-connector.patch").write_text(
        "old patch\n"
    )
    (artifacts / "wheels/ai_dynamo-old.whl").write_bytes(b"old application wheel")
    (artifacts / "wheels/ai_dynamo_runtime-native.whl").write_bytes(
        b"unchanged native wheel"
    )
    (artifacts / "profile.json").write_text(
        json.dumps({"id": "ascend", "base_image": "vendor@sha256:" + "1" * 64})
    )
    files = {
        str(p.relative_to(source)): sha256(p) for p in source.rglob("*") if p.is_file()
    }
    (artifacts / "SOURCE.json").write_text(
        json.dumps({"files": files, "commit": "fixture"})
    )
    (artifacts / "SHA256.json").write_text(
        json.dumps(
            {
                str(p.relative_to(artifacts)): sha256(p)
                for p in artifacts.rglob("*")
                if p.is_file()
            }
        )
    )
    (context / "runtime.Dockerfile").write_text("FROM scratch\n")
    (original / "image.json").write_text(json.dumps({"id": "original-image"}))
    original_hashes = {
        str(p.relative_to(original)): sha256(p)
        for p in original.rglob("*")
        if p.is_file()
    }
    wheel = tmp_path / "ai_dynamo-new.whl"
    wheel.write_bytes(b"new application wheel")
    application_patch = tmp_path / "application.patch"
    application_patch.write_text("fixture application patch\n")
    changes = tmp_path / "changes.json"
    changes.write_text("{}\n")
    output = tmp_path / "rebuilt"
    script = ROOT / IMAGES / "rebuild-app-image.py"
    argv = [str(script)]
    for key, value in {
        "original": original,
        "source": source,
        "wheel": wheel,
        "patch": application_patch,
        "output": output,
        "tag": "fixture-rebuilt:local",
        "changes": changes,
    }.items():
        argv.extend(["--" + key, str(value)])
    if refresh:
        argv.append("--refresh-vendor-patches")
    monkeypatch.setattr(sys, "argv", argv)
    with (
        patch("subprocess.run") as build,
        patch(
            "subprocess.check_output", return_value=b'[{"Id":"rebuilt-image"}]'
        ) as inspect,
    ):
        runpy.run_path(str(script), run_name="__main__")
    build.assert_called_once()
    assert build.call_args.args[0][:4] == ["docker", "build", "--network", "none"]
    inspect.assert_called_once_with(["docker", "inspect", "fixture-rebuilt:local"])
    rebuilt = output / "image-context/artifacts"
    expected_patches = transfer if refresh else artifacts / "vendor-patches"
    for p in expected_patches.rglob("*"):
        if p.is_file():
            assert (
                rebuilt / "vendor-patches" / p.relative_to(expected_patches)
            ).read_bytes() == p.read_bytes()
    for relative, checksum in json.loads((rebuilt / "SHA256.json").read_text()).items():
        assert sha256(rebuilt / relative) == checksum
    assert (rebuilt / "wheels" / wheel.name).read_bytes() == wheel.read_bytes()
    assert not (rebuilt / "wheels/ai_dynamo-old.whl").exists()
    assert (
        rebuilt / "wheels/ai_dynamo_runtime-native.whl"
    ).read_bytes() == b"unchanged native wheel"
    assert (
        json.loads((rebuilt / "REBUILD.json").read_text())["vendor_patches_refreshed"]
        is refresh
    )
    assert {
        str(p.relative_to(original)): sha256(p)
        for p in original.rglob("*")
        if p.is_file()
    } == original_hashes


def test_build_cache_separates_snapshots_with_preserved_timestamps(tmp_path):
    audit = runpy.run_path(str(ROOT / "container/platforms/audit.py"))
    source = tmp_path / "worker.rs"
    manifest = tmp_path / "SOURCE.json"
    source.write_text("old implementation")
    stamp = source.stat()
    manifest.write_text(
        json.dumps({"commit": "first", "files": {"worker.rs": sha256(source)}})
    )
    first = audit["build_cache_key"](tmp_path, "ascend")
    assert first == audit["build_cache_key"](tmp_path, "ascend")
    assert first != audit["build_cache_key"](tmp_path, "metax")
    source.write_text("new implementation")
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="Source snapshot mismatch"):
        audit["build_cache_key"](tmp_path, "ascend")
    manifest.write_text(
        json.dumps({"commit": "second", "files": {"worker.rs": sha256(source)}})
    )
    assert first != audit["build_cache_key"](tmp_path, "ascend")


def test_build_cache_rejects_manifest_path_outside_source(tmp_path):
    audit = runpy.run_path(str(ROOT / "container/platforms/audit.py"))
    source = tmp_path / "source"
    source.mkdir()
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"external")
    (source / "SOURCE.json").write_text(
        json.dumps({"commit": "fixture", "files": {"../foreign": sha256(foreign)}})
    )
    with pytest.raises(ValueError, match="Source snapshot mismatch"):
        audit["build_cache_key"](source, "ascend")
