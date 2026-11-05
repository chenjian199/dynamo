#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read host counters without resetting them; run on the respective device node."""
import argparse
import datetime
import json
import socket
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
a = p.parse_args()
result = {
    "host": socket.gethostname(),
    "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "devices": {},
}
if a.platform == "cuda":
    for dev in sorted(Path("/sys/class/infiniband").glob("*")):
        values = {}
        for group in ["counters", "hw_counters"]:
            for f in sorted((dev / "ports/1" / group).glob("*")):
                try:
                    values[group + "/" + f.name] = int(f.read_text().strip())
                except (OSError, ValueError):
                    continue
        result["devices"][dev.name] = values
else:
    for i in range(8):
        r = subprocess.run(
            ["hccn_tool", "-i", str(i), "-stat", "-g"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode:
            raise RuntimeError(r.stdout + r.stderr)
        values = {}
        for line in r.stdout.splitlines():
            key, sep, value = line.partition(":")
            if sep and value.strip().isdigit():
                values[key.strip()] = int(value.strip())
        result["devices"][str(i)] = values
print(json.dumps(result, indent=2))
