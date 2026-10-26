#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run a platform image; source mounts are restricted to dev images."""

import argparse
import json
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cuda", "metax", "ascend"], required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--devices", default="0", help="Comma-separated physical device IDs"
    )
    parser.add_argument("--models", type=Path, default=Path("/NFS_62/Models"))
    parser.add_argument("--detach", action="store_true")
    parser.add_argument(
        "--interactive", action="store_true", help="Allocate an interactive TTY"
    )
    parser.add_argument(
        "--source", type=Path, help="Dev checkout mounted at /workspace/dynamo"
    )
    parser.add_argument(
        "--cache-volume", help="Dev Cargo build cache volume mounted at /build-cache"
    )
    parser.add_argument(
        "--port",
        action="append",
        default=[],
        help="Docker port mapping, e.g. 8000:8000",
    )
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not re.fullmatch(r"\d+(,\d+)*", args.devices):
        parser.error("--devices must contain numeric device IDs")
    if not args.models.is_dir():
        parser.error("Model root is missing; check the NFS mount first")
    if args.device == "ascend":
        running = subprocess.check_output(["docker", "ps", "-q"], text=True).split()
        selected = {f"/dev/davinci{index}" for index in args.devices.split(",")}
        if running:
            for container in json.loads(
                subprocess.check_output(["docker", "inspect", *running])
            ):
                attached = {
                    d["PathOnHost"]
                    for d in container["HostConfig"].get("Devices") or []
                }
                overlap = sorted(selected & attached)
                if overlap:
                    parser.error(
                        f"Ascend devices {overlap} are mapped by running container {container['Name']}; select unused devices or stop that container first"
                    )
    info = json.loads(
        subprocess.check_output(["docker", "image", "inspect", args.image])
    )[0]
    if f"DYNAMO_IMAGE_PLATFORM={args.device}" not in info["Config"]["Env"]:
        parser.error("Image platform does not match --device")
    if args.source or args.cache_volume:
        if "DYNAMO_IMAGE_TARGET=dev" not in info["Config"]["Env"]:
            parser.error("Source and build cache mounts require a dev image")
    if args.source and not (args.source / "pyproject.toml").is_file():
        parser.error("--source must point to a Dynamo checkout")
    command = [
        "docker",
        "run",
        "--name",
        args.name,
        "--init",
        "--shm-size",
        "8g",
        "--mount",
        f"type=bind,src={args.models.resolve()},dst=/models,readonly",
        "-e",
        "HF_HUB_OFFLINE=1",
    ]
    if args.detach:
        command.append("-d")
    if args.interactive:
        command.append("-it")
    if args.source:
        command.extend(
            ["--mount", f"type=bind,src={args.source.resolve()},dst=/workspace/dynamo"]
        )
    if args.cache_volume:
        command.extend(
            ["--mount", f"type=volume,src={args.cache_volume},dst=/build-cache"]
        )
    for port in args.port:
        command.extend(["-p", port])
    if args.device == "cuda":
        command.extend(["--gpus", f'"device={args.devices}"'])
    elif args.device == "metax":
        command.extend(
            [
                "--device",
                "/dev/dri",
                "--device",
                "/dev/mxcd",
                "-e",
                f"CUDA_VISIBLE_DEVICES={args.devices}",
                "-e",
                f"MACA_VISIBLE_DEVICES={args.devices}",
            ]
        )
    else:
        for device in args.devices.split(","):
            command.extend(["--device", f"/dev/davinci{device}"])
        for path in ("/dev/davinci_manager", "/dev/devmm_svm", "/dev/hisi_hdc"):
            command.extend(["--device", path])
        for path in (
            "/usr/local/Ascend/driver",
            "/usr/local/dcmi",
            "/usr/local/sbin/npu-smi",
        ):
            if not Path(path).exists():
                parser.error(f"Missing Ascend host driver path: {path}")
            command.extend(["--mount", f"type=bind,src={path},dst={path},readonly"])
        local_devices = ",".join(str(i) for i in range(len(args.devices.split(","))))
        command.extend(["-e", f"ASCEND_RT_VISIBLE_DEVICES={local_devices}"])
    for value in args.env:
        command.extend(["-e", value])
    tail = args.command[1:] if args.command[:1] == ["--"] else args.command
    subprocess.run([*command, args.image, *tail], check=True)


if __name__ == "__main__":
    main()
