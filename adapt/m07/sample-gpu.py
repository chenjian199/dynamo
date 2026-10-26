#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Record vendor memory/utilization output while an Omni run is active."""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--platform", choices=["cuda", "metax"], required=True)
parser.add_argument("--run", type=Path, required=True)
parser.add_argument("--devices", default="1,2,3")
args = parser.parse_args()
commands = (
    [
        [
            "nvidia-smi",
            "-i",
            args.devices,
            "--query-gpu=index,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    ]
    if args.platform == "cuda"
    else [
        ["mx-smi", "-i", device, "--show-memory", "--show-usage"]
        for device in args.devices.split(",")
    ]
)
with (args.run / "gpu-samples.jsonl").open("x") as stream:
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        sample = {"timestamp": datetime.now(timezone.utc).isoformat(), "commands": []}
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            sample["commands"].append(
                {
                    "argv": command,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
        stream.write(json.dumps(sample) + "\n")
        stream.flush()
        if (args.run / "result.json").exists():
            break
        time.sleep(30)
