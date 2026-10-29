#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run bounded independent KVBM multi-process acceptance on one vendor host."""

import argparse
import hashlib
import itertools
import json
import os
import platform
import signal
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", choices=("cuda", "metax", "ascend"), required=True
    )
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--library-dir", type=Path, required=True)
    parser.add_argument("--devices", default="2,3")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    devices = args.devices.split(",")
    if (
        len(devices) < 2
        or len(set(devices)) != len(devices)
        or not all(d.isdigit() for d in devices)
    ):
        parser.error("supply at least two distinct physical device ordinals")
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    files = (root / "adapter/m06/sources.txt").read_text().splitlines()
    source = {
        "platform": args.platform,
        "architecture": platform.machine(),
        "binary": str(args.binary),
        "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "files": {
            f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in files
        },
    }
    build_source = args.binary.parent / "build-source.json"
    if build_source.exists():
        source["build"] = json.loads(build_source.read_text())
        if source["build"]["artifacts"][args.binary.name] != source["binary_sha256"]:
            raise RuntimeError("binary differs from build provenance")
    (args.output / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = (
        str(args.library_dir) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    )
    summary = {
        "status": "failed",
        "platform": args.platform,
        "devices": devices,
        "cases": [],
    }
    commands = []
    try:
        for outer, width in itertools.product((1, 2), (2, 4, 8)):
            case = args.output / f"outer-{outer}-width-{width}"
            case.mkdir()
            children = []
            logs = []
            try:
                for rank, device in enumerate(devices):
                    command = [
                        str(args.binary),
                        args.platform,
                        device,
                        str(rank),
                        str(len(devices)),
                        str(case.resolve()),
                        str(outer),
                        str(width),
                    ]
                    commands.append(command)
                    log = (
                        args.output / f"outer-{outer}-width-{width}-rank-{rank}.log.txt"
                    ).open("w")
                    logs.append(log)
                    children.append(
                        subprocess.Popen(
                            command,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                    )
                (args.output / "commands.json").write_text(
                    json.dumps(commands, indent=2) + "\n"
                )
                deadline = time.monotonic() + 180
                while any(p.poll() is None for p in children):
                    failures = [
                        p.returncode for p in children if p.returncode not in (None, 0)
                    ]
                    if failures:
                        raise RuntimeError(f"outer={outer}: rank exited: {failures}")
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"outer={outer}: rank deadline exceeded")
                    time.sleep(0.2)
                if any(p.returncode != 0 for p in children):
                    raise RuntimeError(f"outer={outer}: failed rank")
                for rank in range(len(devices)):
                    text = (
                        args.output / f"outer-{outer}-width-{width}-rank-{rank}.log.txt"
                    ).read_text()
                    if "passed=true" not in text:
                        raise RuntimeError(
                            f"outer={outer}: missing rank {rank} evidence"
                        )
                summary["cases"].append(
                    {
                        "outer": outer,
                        "width": width,
                        "status": "passed",
                        "ranks": len(devices),
                    }
                )
            finally:
                for child in children:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGTERM)
                for child in children:
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                for log in logs:
                    log.close()
        summary["status"] = "passed"
    except Exception as error:
        summary["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
