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

from dynamo._core import backend
from dynamo.common.platform import PlatformRegistry
from dynamo.runtime import DistributedRuntime


async def run(args, report):
    os.environ.update(
        DYN_PLATFORM="cpu", DYN_SYSTEM_PORT="0", DYN_FILE_KV=args.discovery_dir
    )
    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "file", "tcp", event_plane="zmq"
    )
    try:
        cpu = PlatformRegistry().prepare("cpu")
        runtime.configure_platform(cpu)
        runtime.configure_platform(cpu)
        assert runtime.platform["platform_id"] == "cpu"
        backend.WorkerConfig(namespace="platform-contract-test", platform=cpu)
        for key, value in (("schema_version", 99), ("provider_version", "conflicting")):
            invalid = copy.deepcopy(cpu)
            invalid[key] = value
            try:
                runtime.configure_platform(invalid)
            except Exception as error:
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
            assert (
                instance.device_type is None
            ), "Non-CUDA platforms must not advertise legacy CUDA"
            assert (
                platform["schema_version"] == 1
                and platform["platform_id"] == args.platform
            )
            assert platform["device_kind"] == "accelerator"
            assert (
                platform["software"]["vllm"].split("+")[0]
                == {"metax": "0.26.0", "ascend": "0.23.0"}[args.platform]
            )
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
        assert (
            "missing_capability" in diagnostic and "pd.transfer" in diagnostic
        ), diagnostic[-4000:]
        assert not any(
            path.is_file() for path in Path(directory).rglob("*")
        ), "Failed worker published discovery data"
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
    root = Path(__file__).resolve().parents[2]
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
        f"adapt/{args.platform}/{args.platform}-serve.sh",
        f"adapt/{args.platform}/{args.platform}-kv-serve.sh",
        "adapt/metax/metax-platform-smoke.py",
    ]
    sources.extend(
        str(path.relative_to(root))
        for path in (root / "components/src/dynamo/common/platform").glob("*.py")
    )
    report = {
        "passed": False,
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sources
        },
        "cuda_hardware_verified": False,
    }
    try:
        asyncio.run(run(args, report))
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"passed={report['passed']}; evidence={args.output}")


if __name__ == "__main__":
    main()
