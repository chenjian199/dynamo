#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resume a wheel download and require its recorded PyPI SHA256 and size."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--metadata", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument(
    "--url", help="Optional mirror URL; recorded SHA256 and size remain mandatory"
)
a = p.parse_args()
meta = json.loads(a.metadata.read_text())
name = meta["filename"]
if Path(name).name != name or not name.endswith(".whl"):
    p.error("Invalid wheel filename")
a.output.mkdir(parents=True, exist_ok=True)
target = a.output / name
partial = target.with_suffix(target.suffix + ".partial")
if not target.exists():
    for attempt in range(15):
        result = subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--retry",
                "0",
                "--max-time",
                "90",
                "--continue-at",
                "-",
                "--output",
                str(partial),
                a.url or meta["url"],
            ]
        )
        if result.returncode == 0 and partial.stat().st_size == meta["size"]:
            break
    if not partial.exists() or partial.stat().st_size != meta["size"]:
        raise RuntimeError("Incomplete download; partial file retained")
    if hashlib.sha256(partial.read_bytes()).hexdigest() != meta["digests"]["sha256"]:
        raise RuntimeError("Wheel SHA256 mismatch; refusing to install")
    partial.rename(target)
assert target.stat().st_size == meta["size"]
assert hashlib.sha256(target.read_bytes()).hexdigest() == meta["digests"]["sha256"]
print(target, flush=True)
