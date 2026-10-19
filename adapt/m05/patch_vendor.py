#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check/apply/revert the exact M05 vendor connector fixes inside a dev container.

Stop the container's inference workers first. Unknown source hashes are rejected;
this is not a general patch installer for other releases of either engine.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["ascend", "metax"], required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    patches = Path(__file__).with_name("patches")
    entry = json.loads((patches / "manifest.json").read_text())[args.platform]
    version = importlib.metadata.version(entry["distribution"])
    if version.split("+")[0] != entry["version"]:
        raise RuntimeError(f"Unvalidated vendor version: {version}")
    spec = importlib.util.find_spec(entry["package"])
    target = Path(next(iter(spec.submodule_search_locations))) / entry["relative_path"]
    before = digest(target)
    if args.platform == "ascend":
        tp_manifest = json.loads((patches / "ascend-tp-manifest.json").read_text())
        tp_hash = tp_manifest[entry["relative_path"]]["after"]
        if before == tp_hash:
            if args.restore:
                raise RuntimeError(
                    "Restore patch_ascend_tp.py before restoring the base patch"
                )
            print(
                json.dumps(
                    dict(
                        platform=args.platform,
                        version=version,
                        path=str(target),
                        state="patched+tp",
                        sha256=before,
                    ),
                    indent=2,
                )
            )
            return
    original, patched = entry["original_sha256"], entry["patched_sha256"]
    if before not in (original, patched, entry.get("previous_sha256")):
        raise RuntimeError(f"Unknown source hash at {target}: {before}")
    desired = original if args.restore else patched
    if (args.apply or args.restore) and before != desired:
        # Patch a temporary copy, verify the complete output, then atomically
        # replace the module. A failed patch leaves the installed file intact.
        with tempfile.TemporaryDirectory(prefix="m05-", dir=target.parent) as tmp:
            copy = Path(tmp) / target.name
            shutil.copy2(target, copy)
            if before == entry.get("previous_sha256"):
                subprocess.run(
                    [
                        "patch",
                        "--batch",
                        "--reverse",
                        str(copy),
                        str(patches / entry["previous_patch"]),
                    ],
                    check=True,
                    capture_output=True,
                )
                if digest(copy) != original:
                    raise RuntimeError("Failed to normalize the previous patch")
            command = ["patch", "--batch", "--forward"]
            if args.restore and before == entry.get("previous_sha256"):
                os.replace(copy, target)
                print(
                    json.dumps(
                        dict(platform=args.platform, state="original", sha256=original)
                    )
                )
                return
            if args.restore:
                command.append("--reverse")
            subprocess.run(
                [*command, str(copy), str(patches / f"{args.platform}.patch")],
                check=True,
            )
            if digest(copy) != desired:
                raise RuntimeError("Patched source did not match the expected hash")
            os.replace(copy, target)
    print(
        json.dumps(
            {
                "platform": args.platform,
                "version": version,
                "path": str(target),
                "state": (
                    "patched"
                    if digest(target) == patched
                    else "previous-patch"
                    if digest(target) == entry.get("previous_sha256")
                    else "original"
                ),
                "sha256": digest(target),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
