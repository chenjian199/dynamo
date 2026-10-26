#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build pinned incremental images from a clean vendor builder, never docker commit."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    print("+", *args, flush=True)
    return subprocess.run(args, check=True, **kwargs)


def build_dev(args, profile, out):
    if not args.runtime_image or args.tag == args.runtime_image:
        raise ValueError("Dev needs --runtime-image and a distinct output --tag")
    parent = json.loads(
        subprocess.check_output(["docker", "image", "inspect", args.runtime_image])
    )[0]
    if f"DYNAMO_IMAGE_PLATFORM={args.device}" not in parent["Config"]["Env"]:
        raise ValueError("Runtime image platform mismatch")
    if "DYNAMO_IMAGE_TARGET=dev" in parent["Config"]["Env"]:
        raise ValueError("Dev must derive from a runtime image, not another dev image")
    frozen = json.loads(
        subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "/opt/dynamo/venv/bin/python",
                parent["Id"],
                "-c",
                "from pathlib import Path; print(Path('/opt/dynamo/artifacts/profile.json').read_text())",
            ]
        )
    )
    if frozen != profile:
        raise ValueError("Runtime profile differs from checkout; rebuild runtime first")
    if args.resume:
        saved = json.loads((out / "dev-inputs.json").read_text())
        if saved["runtime_id"] != parent["Id"]:
            raise ValueError("Runtime image changed since the initial dev build")
    else:
        out.mkdir(parents=True, exist_ok=False)
        context = out / "dev-context"
        context.mkdir()
        for name in (
            "dev.Dockerfile",
            "dev-setup.sh",
            "dev-editable.sh",
            "dev-check.py",
        ):
            shutil.copy2(ROOT / "container/platforms" / name, context / name)
        shutil.copy2(
            ROOT / "container/requirements/platforms/dev.txt", context / "dev.txt"
        )
        shutil.copy2(ROOT / "rust-toolchain.toml", context / "rust-toolchain.toml")
        saved = {
            "runtime_id": parent["Id"],
            "profile": profile,
            "files": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in context.iterdir()
            },
        }
        (out / "dev-inputs.json").write_text(json.dumps(saved, indent=2) + "\n")
    context = out / "dev-context"
    actual = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in context.iterdir()
    }
    if actual != saved["files"]:
        raise ValueError("Frozen dev build context was modified")
    # Docker's predefined proxy build args are not persisted in the image.
    # Pass names only: never expose proxy credentials in command logs.
    proxy_args = []
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    ):
        if os.environ.get(key):
            proxy_args.extend(["--build-arg", key])
    run(
        "docker",
        "build",
        *proxy_args,
        "--network",
        "host",
        "--build-arg",
        "RUNTIME_IMAGE=" + parent["Id"],
        "--build-arg",
        "RUST_MIRROR=" + args.rust_mirror,
        "-f",
        str(context / "dev.Dockerfile"),
        "-t",
        args.tag,
        str(context),
        env={**os.environ, "DOCKER_BUILDKIT": "0"},
    )
    image = json.loads(
        subprocess.check_output(["docker", "image", "inspect", args.tag])
    )[0]
    image_record = json.dumps(
        {
            "id": image["Id"],
            "tag": args.tag,
            "target": "dev",
            "runtime_id": parent["Id"],
            "profile": profile,
            "inputs": saved,
        },
        indent=2,
    )
    (out / "image.json").write_text(image_record + "\n")
    print("Built dev", args.tag, "from runtime", parent["Id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cuda", "metax", "ascend"], required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--target", choices=["runtime", "dev"], default="runtime")
    parser.add_argument(
        "--runtime-image", help="Existing pinned runtime parent for --target dev"
    )
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rust-mirror", choices=["rsproxy"], default="")
    args = parser.parse_args()
    profile = json.loads(
        (ROOT / "container/platforms" / f"{args.device}.json").read_text()
    )
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    if arch != profile["architecture"]:
        parser.error("Run this build natively on the matching hardware architecture")
    out = args.output.resolve()
    if out.is_relative_to(ROOT):
        parser.error("Build output must be outside the source checkout")
    if args.target == "dev":
        build_dev(args, profile, out)
        return
    if args.runtime_image:
        parser.error("--runtime-image is only valid with --target dev")
    suffix = hashlib.sha256(str(out).encode()).hexdigest()[:8]
    name = f"dynamo-m02-build-{args.device}-{suffix}"
    if not args.resume:
        out.mkdir(parents=True, exist_ok=False)
        files = (
            subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=ROOT,
            )
            .decode()
            .split("\0")
        )
        files = sorted(
            set(
                f
                for f in files
                if f and not f.startswith("reference/") and (ROOT / f).is_file()
            )
        )
        hashes = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files}
        source = {
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "files": hashes,
        }
        (out / "SOURCE.json").write_text(json.dumps(source, indent=2) + "\n")
        with tarfile.open(out / "source.tar", "w") as archive:
            for f in files:
                archive.add(ROOT / f, arcname=f, recursive=False)
            archive.add(out / "SOURCE.json", arcname="SOURCE.json")
        run(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--user",
            "0",
            "--network",
            "host",
            "--mount",
            f"type=bind,src={out},dst=/out",
            "--mount",
            f"type=volume,src={name}-cache,dst=/build-cache",
            "--entrypoint",
            "/bin/bash",
            profile["base_image"],
            "-lc",
            "mkdir -p /src; exec sleep infinity",
        )
    else:
        with tarfile.open(out / "source.tar") as archive:
            frozen = json.load(
                archive.extractfile(f"container/platforms/{args.device}.json")
            )
        if frozen != profile:
            parser.error(
                "Profile differs from source snapshot; use a new output directory"
            )
        run("docker", "start", name)
    # docker run -d does not wait for the shell's initialization commands.
    run("docker", "exec", name, "mkdir", "-p", "/src")
    run("docker", "exec", name, "tar", "-xf", "/out/source.tar", "-C", "/src")
    # No source bind mount: the builder consumes the recorded immutable snapshot.
    run(
        "docker",
        "exec",
        "-e",
        f"CARGO_BUILD_JOBS={args.jobs}",
        "-e",
        f"DYNAMO_RUST_MIRROR={args.rust_mirror}",
        name,
        "bash",
        "/src/container/platforms/build-wheels.sh",
        args.device,
    )
    context = out / "image-context"
    context.mkdir(exist_ok=True)
    artifacts = context / "artifacts"
    artifacts.mkdir(exist_ok=True)
    for item in ("wheels", "native", "vendor-patches", "locks"):
        shutil.copytree(out / item, artifacts / item, dirs_exist_ok=True)
    for item in (
        "SOURCE.json",
        "profile.json",
        "base.json",
        "dependencies.json",
        "protected.txt",
        "build-python.txt",
        "build-os.txt",
        "rustc.txt",
        "runtime-features.txt",
        "kvbm-features.txt",
        "native-build.jsonl",
    ):
        shutil.copy2(out / item, artifacts / item)
    # Seal only the final artifact set, excluding source tar and intermediate caches.
    manifest = {
        str(p.relative_to(artifacts)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(artifacts.rglob("*"))
        if p.is_file() and p.name != "SHA256.json"
    }
    (artifacts / "SHA256.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for item in ("audit.py", "entrypoint.sh", "runtime.Dockerfile"):
        # Read build scripts from the same source snapshot, not a moving checkout.
        run(
            "docker",
            "cp",
            name + ":/src/container/platforms/" + item,
            str(context / item),
        )
    run(
        "docker",
        "build",
        "--network",
        "host",
        "--build-arg",
        "BASE_IMAGE=" + profile["base_image"],
        "--build-arg",
        "ACCELERATOR=" + args.device,
        "-f",
        str(context / "runtime.Dockerfile"),
        "-t",
        args.tag,
        str(context),
        env={**os.environ, "DOCKER_BUILDKIT": "0"},
    )
    image = json.loads(
        subprocess.check_output(["docker", "image", "inspect", args.tag])
    )[0]
    image_record = json.dumps(
        {
            "id": image["Id"],
            "tag": args.tag,
            "profile": profile,
            "source_commit": json.loads((out / "SOURCE.json").read_text())["commit"],
        },
        indent=2,
    )
    (out / "image.json").write_text(image_record + "\n")
    run("docker", "stop", name)
    print("Built", args.tag, "evidence:", out)


if __name__ == "__main__":
    main()
