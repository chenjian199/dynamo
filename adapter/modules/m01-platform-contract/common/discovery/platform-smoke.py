#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify M01 discovery metadata and fail-closed startup against live KV workers."""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def validate_worker_stack(platform, profile, software):
    if profile["id"] != platform:
        raise AssertionError("Stack profile belongs to a different platform")
    for package, expected in profile["versions"].items():
        actual = software.get(package)
        if actual != expected:
            raise AssertionError(f"{package}: expected {expected}, observed {actual}")


def source_identity(root, sources):
    hashes = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources
    }
    # Exported source has no Git metadata. Its manifest must cover every input
    # read by this probe, including the probe and the canonical platform lock.
    manifest = root / "SOURCE.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text())
        for name, value in hashes.items():
            if data["files"].get(name) != value:
                raise AssertionError(f"Source manifest mismatch: {name}")
        commit = data["commit"]
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise ValueError("Invalid source commit")
        return commit, hashes
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    return commit, hashes


async def run(args, report):
    from dynamo._core import backend
    from dynamo.common.platform import PlatformRegistry
    from dynamo.common.platform.registry import PlatformError
    from dynamo.runtime import DistributedRuntime

    try:
        PlatformRegistry().prepare("heyi-unregistered-negative-probe")
    except PlatformError as error:
        assert error.code == "unknown_platform"
        report["unknown_platform_rejection"] = error.to_dict()
    else:
        raise AssertionError("Unknown platform was accepted")
    os.environ.update(
        DYN_PLATFORM="cpu", DYN_SYSTEM_PORT="0", DYN_FILE_KV=args.discovery_dir
    )
    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "file", "tcp", event_plane="zmq"
    )
    try:
        cpu = PlatformRegistry().prepare("cpu")
        report["explicit_cpu_selection"] = cpu
        runtime.configure_platform(cpu)
        runtime.configure_platform(cpu)
        assert runtime.platform["platform_id"] == "cpu"
        backend.WorkerConfig(namespace="platform-contract-test", platform=cpu)
        for key, value in (("schema_version", 99), ("provider_version", "conflicting")):
            invalid = copy.deepcopy(cpu)
            invalid[key] = value
            try:
                runtime.configure_platform(invalid)
            except Exception as error:  # noqa: BLE001 -- Rust to_pyerr returns PyException; check its exact diagnostic below.
                expected = (
                    "unsupported_schema"
                    if key == "schema_version"
                    else "platform already frozen with a different configuration"
                )
                assert expected in str(error), str(error)
                report.setdefault("configuration_rejections", {})[key] = str(error)
            else:
                raise AssertionError(f"Accepted invalid configuration: {key}")
        client = await runtime.endpoint(f"{args.namespace}.backend.generate").client()
        await asyncio.wait_for(client.wait_for_instances(), timeout=30)
        for _ in range(100):
            instances = client.instances()
            if len(instances) == 2:
                break
            await asyncio.sleep(0.1)
        assert len(instances) == 2, f"Expected two KV workers, got {len(instances)}"
        workers = []
        for instance in instances:
            platform = instance.platform
            assert instance.device_type is None, (
                "Non-CUDA platforms must not advertise legacy CUDA"
            )
            assert (
                platform["schema_version"] == 1
                and platform["platform_id"] == args.platform
            )
            assert platform["device_kind"] == "accelerator"
            validate_worker_stack(args.platform, report["stack"], platform["software"])
            assert (
                platform["software"]["driver"]
                and platform["software"][
                    "maca_runtime" if args.platform == "metax" else "cann"
                ]
            )
            assert len(platform["devices"]) == 1
            device = platform["devices"][0]
            assert (
                device["uuid"] and device["memory_bytes"] > 0 and device["ordinal"] == 0
            )
            assert platform["capabilities"]["kv.events"]["state"] == "available"
            assert platform["capabilities"]["pd.transfer"]["state"] == "unknown"
            workers.append(
                {
                    "instance_id": instance.instance_id,
                    "device_type": instance.device_type,
                    "platform": platform,
                }
            )
        assert (
            workers[0]["platform"]["devices"][0]["uuid"]
            != workers[1]["platform"]["devices"][0]["uuid"]
        )
        report["workers"] = workers
    finally:
        runtime.shutdown()

    with tempfile.TemporaryDirectory(
        prefix=f"{args.platform}-m01-negative-"
    ) as directory:
        environment = dict(
            os.environ,
            DYN_PLATFORM=args.platform,
            DYN_REQUIRED_CAPABILITIES="pd.transfer",
            DYN_NAMESPACE=f"{args.platform}-m01-reject",
            DYN_FILE_KV=directory,
            HF_HUB_OFFLINE="1",
        )
        environment[
            "CUDA_VISIBLE_DEVICES"
            if args.platform == "metax"
            else "ASCEND_RT_VISIBLE_DEVICES"
        ] = args.probe_gpu
        command = [
            sys.executable,
            "-m",
            "dynamo.vllm",
            "--model",
            args.model_path,
            "--enforce-eager",
            "--max-model-len",
            "4096",
            "--max-num-seqs",
            "1",
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=120)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        diagnostic = output.decode(errors="replace")
        assert process.returncode != 0, "Unknown required capability must fail startup"
        assert "missing_capability" in diagnostic and "pd.transfer" in diagnostic, (
            diagnostic[-4000:]
        )
        assert not any(path.is_file() for path in Path(directory).rglob("*")), (
            "Failed worker published discovery data"
        )
        report["required_capability_rejection"] = {
            "capability": "pd.transfer",
            "exit_code": process.returncode,
            "diagnostic": next(
                line
                for line in diagnostic.splitlines()
                if "PlatformError:" in line and "missing_capability" in line
            ),
            "discovery_files": 0,
        }
    report["passed"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("metax", "ascend"), default="metax")
    parser.add_argument("--namespace", default="metax-kv")
    parser.add_argument("--discovery-dir", default="/tmp/dynamo-metax-kv")
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--probe-gpu", default="3")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[5]
    sources = [
        "Cargo.lock",
        "lib/bindings/python/Cargo.lock",
        "lib/bindings/kvbm/Cargo.lock",
        "lib/runtime/examples/Cargo.lock",
        "lib/platform/src/lib.rs",
        "lib/runtime/src/distributed.rs",
        "lib/runtime/src/component.rs",
        "lib/runtime/src/component/endpoint.rs",
        "lib/runtime/src/pipeline/network/egress/push_router.rs",
        "lib/bindings/python/rust/lib.rs",
        "lib/bindings/python/rust/backend.rs",
        "lib/backend-common/src/worker.rs",
        "components/src/dynamo/vllm/main.py",
        f"adapter/platforms/{args.platform}/bringup/{args.platform}-serve.sh",
        f"adapter/platforms/{args.platform}/bringup/{args.platform}-kv-serve.sh",
        "adapter/modules/m01-platform-contract/common/discovery/platform-smoke.py",
        f"container/platforms/{args.platform}.json",
    ]
    sources.extend(
        str(path.relative_to(root))
        for path in (root / "components/src/dynamo/common/platform").glob("*.py")
    )
    commit, hashes = source_identity(root, sources)
    report = {
        "passed": False,
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_commit": commit,
        "source_sha256": hashes,
        "stack": json.loads(
            (root / f"container/platforms/{args.platform}.json").read_text()
        ),
        "cuda_hardware_verified": False,
    }
    try:
        asyncio.run(run(args, report))
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"passed={report['passed']}; evidence={args.output}")


if __name__ == "__main__":
    main()
