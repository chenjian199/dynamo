#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Package Python candidate sources and exact hashes for the M09 test overlay."""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
paths = (
    subprocess.check_output(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "components/src/dynamo",
            "adapter/m09",
        ],
        cwd=ROOT,
    )
    .decode()
    .split("\0")[:-1]
)
paths = sorted({p for p in paths if "/results/" not in p and "__pycache__" not in p})
manifest = {
    "head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(),
    "files": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
}
with tarfile.open(args.output, "w:gz") as archive:
    for path in paths:
        archive.add(ROOT / path, arcname=path)
    data = json.dumps(manifest, indent=2).encode()
    info = tarfile.TarInfo("SOURCE.json")
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))
print(hashlib.sha256(args.output.read_bytes()).hexdigest(), args.output)
