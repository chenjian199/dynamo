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


def verify_rank(case, rank, world, device, outer, width):
    """Verify SDK counters and actual readback bytes, not the binary's pass text."""
    observation = json.loads((case / f"rank-{rank}.measurements.json").read_text())
    expected = {
        "rank": rank,
        "world": world,
        "device": int(device),
        "outer": outer,
        "width": width,
    }
    if any(observation.get(key) != value for key, value in expected.items()):
        raise ValueError("Native rank identity differs from launched topology")
    counts = observation["sdk_create_release_counts"]
    if (
        len(counts) != 8
        or any(type(value) is not int or value < 0 for value in counts)
        or any(counts[i] != counts[i + 1] for i in range(0, 8, 2))
        or counts[0] == 0
        or observation["metrics"]["live_allocations"] != 0
    ):
        raise ValueError("Native SDK allocation/handle reclamation mismatch")
    if observation["completed_operations"] <= 0:
        raise ValueError("No native transfer was submitted")
    expected_layout = {
        "num_layers": 3,
        "num_blocks": 4,
        "page_size": 4,
        "num_heads": 2,
        "inner_dim": 16,
        "dtype_width_bytes": width,
    }
    if observation["layout"] != expected_layout:
        raise ValueError("Native rank layout differs from the exercised layout")
    bytes_per_block = 3 * outer * 4 * 16 * width
    files = {}
    for actual, reference in [
        (f"rank-{rank}.shard-actual.bin", f"rank-{rank}.shard-reference.bin"),
        (f"rank-{rank}.G2.actual.bin", f"rank-{rank}.reference.bin"),
        (f"rank-{rank}.G3.actual.bin", f"rank-{rank}.reference.bin"),
    ]:
        data, expected_data = (
            (case / actual).read_bytes(),
            (case / reference).read_bytes(),
        )
        if len(data) != bytes_per_block or data != expected_data:
            raise ValueError("Native readback parity failed: " + actual)
        for name, content in [(actual, data), (reference, expected_data)]:
            files[name] = hashlib.sha256(content).hexdigest()
    if (
        observation["shard_bytes"] != bytes_per_block
        or observation["tier_readback_bytes"] != [bytes_per_block] * 2
    ):
        raise ValueError("Native measured transfer sizes differ")
    return {"observation": observation, "sha256": files}


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
    root = Path(__file__).resolve().parents[5]
    files = (
        (root / "adapter/modules/m06-kvbm-collectives/common/cache/sources.txt")
        .read_text()
        .splitlines()
    )
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
        if source["build"]["files"] != source["files"]:
            raise RuntimeError("sources differ from native build provenance")
        library = args.library_dir / "libkvbm_kernels.so"
        if (
            hashlib.sha256(library.read_bytes()).hexdigest()
            != source["build"]["artifacts"][library.name]
        ):
            raise RuntimeError("library differs from build provenance")
    else:
        # Installed runtime images use the enclosing immutable artifact manifest.
        artifacts = args.binary.parent.parent / "artifacts"
        manifest = json.loads((artifacts / "SHA256.json").read_text())
        exported = json.loads((artifacts / "SOURCE.json").read_text())
        for name, actual in source["files"].items():
            if exported["files"].get(name) != actual:
                raise RuntimeError("packaged native source mismatch: " + name)
        for artifact in (args.binary, args.library_dir / "libkvbm_kernels.so"):
            if (
                hashlib.sha256(artifact.read_bytes()).hexdigest()
                != manifest["native/" + artifact.name]
            ):
                raise RuntimeError(
                    "packaged native artifact mismatch: " + artifact.name
                )
        source["build"] = {
            "source_commit": exported["commit"],
            "manifest_sha256": hashlib.sha256(
                (artifacts / "SHA256.json").read_bytes()
            ).hexdigest(),
        }
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
                measured = [
                    verify_rank(case, rank, len(devices), device, outer, width)
                    for rank, device in enumerate(devices)
                ]
                # Distinct shard references prove this phase was rank-local;
                # replicated tier readbacks must instead agree across ranks.
                shard_hashes = [
                    m["sha256"][f"rank-{rank}.shard-actual.bin"]
                    for rank, m in enumerate(measured)
                ]
                replicated_hashes = [
                    m["sha256"][f"rank-{rank}.G3.actual.bin"]
                    for rank, m in enumerate(measured)
                ]
                if (
                    len(set(shard_hashes)) != len(devices)
                    or len(set(replicated_hashes)) != 1
                ):
                    raise ValueError("Sharded/replicated rank data semantics differ")
                summary["cases"].append(
                    {
                        "outer": outer,
                        "width": width,
                        "status": "passed",
                        "ranks": len(devices),
                        "measurements": measured,
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
