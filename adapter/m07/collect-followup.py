#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Copy bounded M07 follow-up evidence, retaining raw hashes and failed verdicts."""

import argparse
import hashlib
import json
import re
from pathlib import Path

LICENSE = (
    "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\n"
    "SPDX-License-Identifier: Apache-2.0\n"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for path in sorted(args.raw.rglob("*")):
        if not path.is_file() or path.suffix not in (
            ".json",
            ".jsonl",
            ".log",
            ".txt",
            ".exit",
            ".yaml",
            ".yml",
        ):
            continue
        relative = path.relative_to(args.raw)
        if "discovery" in relative.parts or path.name == "response.json":
            continue
        data = path.read_bytes()
        if len(data) > 16 * 1024**2:
            continue
        manifest[str(relative)] = {
            "raw_sha256": hashlib.sha256(data).hexdigest(),
            "raw_bytes": len(data),
        }
        target = args.output / relative
        if path.suffix in (".log", ".txt"):
            target = target.with_suffix(".txt")
            lines = re.sub(
                r"\x1b\[[0-9;]*m", "", data.decode(errors="replace")
            ).splitlines()
            # Full raw logs remain in the run directory. Preserve fault stacks
            # and the end-of-run verdict without committing megabytes of warnings.
            if len(lines) > 240:
                lines = [
                    "[excerpt: final 240 lines; full raw hash in manifest]"
                ] + lines[-240:]
            data = ("\n".join(line.rstrip() for line in lines).rstrip() + "\n").encode()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        Path(str(target) + ".license").write_text(LICENSE)
        manifest[str(relative)]["saved_as"] = str(target.relative_to(args.output))
    path = args.output / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    Path(str(path) + ".license").write_text(LICENSE)


if __name__ == "__main__":
    main()
