#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Install/revert the version- and hash-guarded Ascend TP1 -> TP2 extension.

Apply the base M05 Mooncake patch first; stop inference workers before mutation.
All files are checked and staged before replacement. Original vendor behavior is
preserved unless the connector explicitly sets heyi_tp_split=true.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if importlib.metadata.version("vllm-ascend").split("+")[0] != "0.23.0":
        raise RuntimeError("Only the validated Ascend 0.23.0 source is supported")
    root = Path(importlib.util.find_spec("vllm_ascend").origin).parent
    patches = Path(__file__).with_name("patches")
    manifest = json.loads((patches / "ascend-tp-manifest.json").read_text())
    before, staged, result = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix="m05-tp-", dir=root) as tmp:
        for index, (relative, item) in enumerate(manifest.items()):
            path = root / relative
            data = path.read_bytes()
            sha = digest(data)
            if sha not in (item["before"], item["after"]):
                raise RuntimeError(
                    f"Unknown source {path}: {sha}; apply base M05 patch first"
                )
            before[path] = data
            desired = item["before"] if args.restore else item["after"]
            if (args.apply or args.restore) and sha != desired:
                copy = Path(tmp) / str(index)
                copy.write_bytes(data)
                command = ["patch", "--batch", "--forward"]
                if args.restore:
                    command.append("--reverse")
                subprocess.run(
                    [*command, str(copy), str(patches / item["patch"])],
                    check=True,
                    capture_output=True,
                )
                if digest(copy.read_bytes()) != desired:
                    raise RuntimeError(f"Unexpected staged source {path}")
                copy.chmod(path.stat().st_mode)
                staged[path] = copy
                sha = desired
            result[relative] = dict(
                sha256=sha, state="patched" if sha == item["after"] else "base"
            )
        replaced = []
        try:
            for path, copy in staged.items():
                os.replace(copy, path)
                replaced.append(path)
        except BaseException:
            for path in replaced:
                path.write_bytes(before[path])
            raise
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
