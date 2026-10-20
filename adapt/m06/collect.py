#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect bounded M06 evidence without copying model files or full server logs."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

NOTICE = (
    "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\n"
    "SPDX-License-Identifier: Apache-2.0\n"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    args.target.mkdir(parents=True, exist_ok=False)
    for name in ["summary.json", "source.json", "commands.json"]:
        path = args.source / name
        if path.exists():
            shutil.copyfile(path, args.target / name)
    logs = sorted(args.source.glob("*.log.txt"))
    if not logs:
        raise RuntimeError(f"No engine logs in {args.source}")
    for log in logs:
        collect_log(log, args.target)
    for path in args.target.iterdir():
        if path.is_file() and not path.name.endswith(".license"):
            path.with_name(path.name + ".license").write_text(NOTICE)


def collect_log(log, target):
    prefix = log.name.removesuffix(".log.txt")
    raw = log.read_bytes()
    text = re.sub(r"\x1b\[[0-9;]*m", "", raw.decode(errors="replace"))
    selected = [
        line
        for line in text.splitlines()
        if any(
            word in line
            for word in (
                "platform KVBM",
                "passed=true",
                "KVBM",
                "num_gpu_blocks",
                "KV cache size",
                "RuntimeError",
                "ValueError",
                "panicked",
                "ERROR",
                "NIXL",
                "Finished sending KV transfer",
                "Incompatible PD peer",
            )
        )
    ]
    if len(selected) > 200:
        selected = selected[:60] + ["[intermediate lines omitted]"] + selected[-140:]
    (target / f"{prefix}-excerpt.txt").write_text("\n".join(selected) + "\n")
    provenance = "log-source.json" if prefix == "engine" else f"{prefix}-source.json"
    (target / provenance).write_text(
        json.dumps(
            {
                "path": str(log),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
