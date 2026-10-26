# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Artifact integrity and vendor-stack isolation for incremental images."""

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.gpu_0,
    pytest.mark.core,
    pytest.mark.parallel,
]


@pytest.fixture
def audit():
    spec = importlib.util.spec_from_file_location(
        "platform_audit", Path(__file__).parents[1] / "platforms/audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(audit, monkeypatch, action, path):
    monkeypatch.setattr(audit.sys, "argv", ["audit.py", action, str(path)])
    audit.main()


def test_sealed_artifacts_reject_corruption(audit, monkeypatch, tmp_path):
    wheel = tmp_path / "package.whl"
    wheel.write_bytes(b"original")
    invoke(audit, monkeypatch, "seal", tmp_path)
    invoke(audit, monkeypatch, "verify", tmp_path)
    wheel.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="verification failed"):
        invoke(audit, monkeypatch, "verify", tmp_path)


def test_sealed_artifacts_reject_extra_wheel(audit, monkeypatch, tmp_path):
    invoke(audit, monkeypatch, "seal", tmp_path)
    (tmp_path / "unexpected.whl").write_bytes(b"unexpected dependency")
    with pytest.raises(RuntimeError, match="file set"):
        invoke(audit, monkeypatch, "verify", tmp_path)


def test_sealed_artifacts_reject_external_symlink(audit, monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    external = tmp_path / "external"
    external.write_bytes(b"outside")
    (artifacts / "package.whl").symlink_to(external)
    invoke(audit, monkeypatch, "seal", artifacts)
    with pytest.raises(RuntimeError, match="verification failed"):
        invoke(audit, monkeypatch, "verify", artifacts)


@pytest.mark.parametrize("platform", ["cuda", "metax", "ascend"])
def test_profile_protects_engine_and_torch(platform):
    profile = json.loads(
        (Path(__file__).parents[1] / "platforms" / f"{platform}.json").read_text()
    )
    assert set(profile["versions"]) <= set(profile["protected_packages"])
    assert {"vllm", "torch"} <= set(profile["versions"])
    assert "@sha256:" in profile["base_image"]
    assert "mm-routing" in profile["runtime_features"]
    assert ("nccl" in profile["kvbm_features"]) == (platform == "cuda")


def test_native_artifacts_use_cargo_c_abi_output(audit, monkeypatch, tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    helper = build / "libkvbm_kernels.so"
    helper.write_bytes(b"C ABI helper")
    executable = tmp_path / "native_replicated"
    executable.write_bytes(b"diagnostic")
    executable.chmod(0o755)
    # A same-named Rust cdylib must never be selected as the C ABI library.
    (tmp_path / "libkvbm_kernels.so").write_bytes(b"Rust cdylib")
    records = [
        {
            "reason": "build-script-executed",
            "package_id": "path#kvbm-kernels@1.5.1",
            "out_dir": str(build),
        },
        {
            "reason": "compiler-artifact",
            "target": {"name": "native_replicated"},
            "executable": str(executable),
        },
    ]
    log = tmp_path / "cargo.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in records))
    destination = tmp_path / "native"
    destination.mkdir()
    monkeypatch.setattr(
        audit.sys, "argv", ["audit.py", "native", str(log), str(destination)]
    )
    audit.main()
    assert (destination / helper.name).read_bytes() == b"C ABI helper"
    assert (destination / executable.name).stat().st_mode & 0o111


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).parents[1] / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dev_rejects_different_runtime_profile(monkeypatch, tmp_path):
    from types import SimpleNamespace

    builder = load_script("platform-build")
    commands = []

    def output(argv):
        commands.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": "sha256:parent",
                        "Config": {"Env": ["DYNAMO_IMAGE_PLATFORM=cuda"]},
                    }
                ]
            ).encode()
        return json.dumps({"id": "cuda", "versions": {"vllm": "wrong"}}).encode()

    monkeypatch.setattr(builder.subprocess, "check_output", output)
    args = SimpleNamespace(runtime_image="runtime", tag="dev", device="cuda")
    with pytest.raises(ValueError, match="profile differs"):
        builder.build_dev(
            args, {"id": "cuda", "versions": {"vllm": "0.28.0"}}, tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()
    assert not any(command[:2] == ["docker", "build"] for command in commands)


def test_runtime_rejects_source_mount(monkeypatch, tmp_path):
    runner = load_script("platform-run")
    monkeypatch.setattr(
        "sys.argv",
        [
            "platform-run.py",
            "--device",
            "cuda",
            "--image",
            "runtime",
            "--name",
            "test",
            "--models",
            str(tmp_path),
            "--source",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(
        runner.subprocess,
        "check_output",
        lambda _: json.dumps(
            [{"Config": {"Env": ["DYNAMO_IMAGE_PLATFORM=cuda"]}}]
        ).encode(),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("A runtime image must not start with source mounts")

    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    with pytest.raises(SystemExit) as caught:
        runner.main()
    assert caught.value.code == 2


def test_dev_proxy_args_do_not_expose_values(monkeypatch, tmp_path):
    from types import SimpleNamespace

    builder = load_script("platform-build")
    profile = {"id": "cuda"}
    calls = []

    def output(argv):
        if argv[:3] == ["docker", "image", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": "sha256:parent",
                        "Config": {"Env": ["DYNAMO_IMAGE_PLATFORM=cuda"]},
                    }
                ]
            ).encode()
        return json.dumps(profile).encode()

    monkeypatch.setattr(builder.subprocess, "check_output", output)
    monkeypatch.setattr(builder, "run", lambda *args, **kwargs: calls.append(args))
    proxy = "http://example-user:example-password@proxy.invalid:3128"
    monkeypatch.setenv("HTTP_PROXY", proxy)
    args = SimpleNamespace(
        runtime_image="runtime", tag="dev", device="cuda", resume=False, rust_mirror=""
    )
    builder.build_dev(args, profile, tmp_path / "out")
    assert "HTTP_PROXY" in calls[0]
    assert proxy not in str(calls)
    assert proxy not in (tmp_path / "out/image.json").read_text()
