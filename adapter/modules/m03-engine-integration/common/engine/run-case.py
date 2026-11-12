#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run one manual hardware case, keeping logs and stopping only its own processes."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def source_identity(root, mode, manifest):
    """Keep installed product provenance separate from the invoking test checkout."""
    if mode == "installed":
        raw = manifest.read_bytes()
        commit = json.loads(raw)["commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Installed source manifest has no full commit SHA")
        expected = os.environ.get("DYNAMO_RELEASE_SOURCE_COMMIT")
        if expected and expected != commit:
            raise ValueError(
                "Requested source commit differs from the installed runtime"
            )
        return {
            "source_mode": mode,
            "source_commit": commit,
            "installed_source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        }
    return {
        "source_mode": mode,
        "source_commit": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
    }


def shutdown_succeeded(returncode, forced, log):
    """A wrapper exit alone cannot establish clean engine shutdown."""
    fatal = re.search(
        r"Fatal Python error:|FATAL: exception not rethrown|"
        r"terminate called after throwing|Segmentation fault|core dumped",
        log,
    )
    return returncode == 0 and not forced and fatal is None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=600)
    parser.add_argument(
        "--launch-script",
        type=Path,
        default=Path("adapter/modules/m03-engine-integration/common/engine/serve.sh"),
    )
    parser.add_argument(
        "--check", choices=("text", "lora", "vision", "spec"), default="text"
    )
    parser.add_argument(
        "--source-mode", choices=("checkout", "installed"), default="checkout"
    )
    parser.add_argument(
        "--installed-source-manifest",
        type=Path,
        default=Path("/opt/dynamo/artifacts/SOURCE.json"),
    )
    parser.add_argument("engine_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[5]
    platform = os.environ["PLATFORM"]
    dp = int(os.environ.get("DP", "1"))
    block = 128 if platform == "ascend" else 64
    extra = args.engine_args
    if extra[:1] == ["--"]:
        extra = extra[1:]
    command = ["bash", str(root / args.launch_script), *extra]
    report = dict(
        passed=False,
        started=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        command=command,
        environment={
            key: os.environ.get(key)
            for key in ("PLATFORM", "TP", "DP", "DEVICES", "MODEL_PATH")
        },
        **source_identity(root, args.source_mode, args.installed_source_manifest),
        packages={
            name: importlib.metadata.version(name)
            for name in ("vllm", "torch", "ai-dynamo")
            + (() if platform == "cuda" else (f"vllm-{platform}",))
        },
        harness_sha256={
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for base in (
                "adapter/modules/m03-engine-integration",
                "adapter/modules/m07-gms-multimodal",
            )
            for path in (root / base).rglob("*")
            if path.suffix in (".py", ".sh")
        },
    )
    model_path = Path(os.environ["MODEL_PATH"])
    report["model_config"] = json.loads((model_path / "config.json").read_text())
    revision_path = model_path / "dynamo-model-revision.json"
    if revision_path.exists():
        report["model_revision"] = json.loads(revision_path.read_text())
    report["model_config_sha256"] = hashlib.sha256(
        (model_path / "config.json").read_bytes()
    ).hexdigest()
    report["model_files"] = {
        path.name: dict(size=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns)
        for path in sorted(model_path.glob("*.safetensors"))
    }
    # Check reserved listeners even when an existing service is unhealthy.
    ports = [8010, 8090, *range(20092, 20092 + dp), *range(20094, 20094 + dp)]
    for port in ports:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", port))
    log_path = args.output / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + args.startup_timeout
            while True:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Server exited with {process.returncode}; see server.log"
                    )
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8010/v1/models/m03-model/ready", timeout=3
                    ) as response:
                        if json.load(response).get("ready"):
                            break
                except (urllib.error.URLError, TimeoutError):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Server readiness exceeded {args.startup_timeout} seconds"
                    )
                time.sleep(2)
            with (args.output / "checks.log").open("w") as checks:

                def check(script, *argv):
                    subprocess.run(
                        [sys.executable, str(root / script), *map(str, argv)],
                        check=True,
                        timeout=240,
                        stdout=checks,
                        stderr=subprocess.STDOUT,
                    )

                if args.check != "vision":
                    check(
                        "adapter/platforms/metax/bringup/metax-smoke.py",
                        "--url",
                        "http://127.0.0.1:8010",
                        "--model",
                        "m03-model",
                        "--worker-url",
                        "http://127.0.0.1:8090",
                        "--namespace",
                        f"m03_{platform}",
                        "--output",
                        args.output / "api.json",
                    )
                    capacities = [
                        int(value.replace(",", "")) // block
                        for value in re.findall(
                            r"(?:GPU|NPU) KV cache size: ([\d,]+) tokens",
                            log_path.read_text(),
                        )
                    ]
                    assert capacities and len(set(capacities)) == 1, capacities
                    report["engine_blocks_per_rank"] = capacities[0]
                    check(
                        "adapter/modules/m03-engine-integration/common/engine/rank-smoke.py",
                        "--platform",
                        platform,
                        "--dp",
                        dp,
                        "--block-size",
                        block,
                        "--expected-blocks",
                        capacities[0],
                        "--output",
                        args.output / "kv.json",
                    )
                if args.check != "text":
                    check(
                        "adapter/modules/m03-engine-integration/common/engine/advanced-smoke.py",
                        args.check,
                        "--platform",
                        platform,
                        "--output",
                        args.output / "advanced.json",
                    )
            report["passed"] = True
        except Exception as error:
            report["error"] = repr(error)
            raise
        finally:
            try:
                os.kill(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            forced = False
            try:
                process.wait(timeout=45)
                report["shutdown"] = "exited"
            except subprocess.TimeoutExpired:
                forced = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                report["shutdown"] = "forced after 45 seconds"
            report["server_returncode"] = process.returncode
            report["shutdown_passed"] = shutdown_succeeded(
                process.returncode, forced, log_path.read_text(errors="replace")
            )
            checks_passed = report["passed"]
            report["passed"] = checks_passed and report["shutdown_passed"]
            report["server_log"] = dict(
                path=str(log_path),
                sha256=hashlib.sha256(log_path.read_bytes()).hexdigest(),
            )
            (args.output / "case.json").write_text(json.dumps(report, indent=2) + "\n")
            if checks_passed and not report["shutdown_passed"]:
                raise RuntimeError(
                    "Engine shutdown failed; see case.json and server.log"
                )


if __name__ == "__main__":
    main()
