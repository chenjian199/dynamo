#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the packaged independent KVBM binary, without Rust or source files."""

import argparse
import hashlib
import itertools
import json
import os
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", choices=["cuda", "metax", "ascend"], required=True
    )
    parser.add_argument("--devices", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    devices = args.devices.split(",")
    if (
        len(devices) != 2
        or len(set(devices)) != 2
        or not all(d.isdigit() for d in devices)
    ):
        parser.error("Select two distinct device ordinals")
    args.output.mkdir(parents=True, exist_ok=False)
    binary = Path("/opt/dynamo/native/native_replicated")
    report = {
        "passed": False,
        "platform": args.platform,
        "devices": devices,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "cases": [],
    }
    env = dict(os.environ)
    # The raw SDK uses device ordinals; framework-level visibility is unnecessary.
    for key in (
        "CUDA_VISIBLE_DEVICES",
        "MACA_VISIBLE_DEVICES",
        "ASCEND_RT_VISIBLE_DEVICES",
    ):
        env.pop(key, None)
    try:
        for outer, width in itertools.product((1, 2), (2, 4, 8)):
            case = args.output / f"outer-{outer}-width-{width}"
            case.mkdir()
            processes, logs, commands = [], [], []
            deadline = time.monotonic() + 180
            try:
                for rank, device in enumerate(devices):
                    command = [
                        str(binary),
                        args.platform,
                        device,
                        str(rank),
                        "2",
                        str(case),
                        str(outer),
                        str(width),
                    ]
                    commands.append(command)
                    handle = (case / f"rank-{rank}.log").open("w")
                    logs.append(handle)
                    processes.append(
                        subprocess.Popen(
                            command, env=env, stdout=handle, stderr=subprocess.STDOUT
                        )
                    )
                for process in processes:
                    code = process.wait(timeout=max(1, deadline - time.monotonic()))
                    if code:
                        raise RuntimeError(
                            f"Native rank failed with status {code}: {case}"
                        )
                for rank in range(2):
                    assert "passed=true" in (case / f"rank-{rank}.log").read_text()
                report["cases"].append(
                    {
                        "outer": outer,
                        "width": width,
                        "passed": True,
                        "commands": commands,
                    }
                )
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
                for handle in logs:
                    handle.close()
        report["passed"] = True
    finally:
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases": len(report["cases"])}))


if __name__ == "__main__":
    main()
