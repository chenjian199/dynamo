#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Seal a validated platform delivery directory and export its Docker images."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from image_helpers import inspect_images, save_command


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(root, report):
    if not report.get("platform") or not isinstance(report["platform"], str):
        raise ValueError("Platform identifier required")
    if report["platform"] not in ("cuda", "ascend", "metax") and report.get(
        "architecture"
    ) not in ("amd64", "arm64"):
        raise ValueError("New platform deliveries require explicit architecture")
    if not report.get("checks") or not report.get("images"):
        raise ValueError("Missing test evidence or image inventory")
    roles = {image["role"] for image in report["images"]}
    if not {"runtime", "dev", "tools", "operator"} <= roles:
        raise ValueError("runtime/dev/tools/operator images must all be included")
    for check in report["checks"]:
        if check["status"] not in ("passed", "limited", "deferred", "failed"):
            raise ValueError(f"Incomplete check: {check['name']}")
        if check["status"] != "passed" and not check.get("reason"):
            raise ValueError(f"Non-passing check requires a reason: {check['name']}")
        if check.get("required", True) and check["status"] != "passed":
            raise ValueError(f"Required check did not pass: {check['name']}")
        if check["status"] == "passed" and not check.get("evidence"):
            raise ValueError(f"Passing check has no evidence: {check['name']}")
        for relative, expected in check.get("evidence", {}).items():
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError(f"Invalid evidence path: {relative}")
            if sha256(path) != expected:
                raise ValueError(f"Evidence hash mismatch: {relative}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        required=True,
        help="Prepared delivery directory containing validation.json",
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--prepared-export",
        action="store_true",
        help="Use a complete, hash-checked image-export.json in the delivery directory",
    )
    args = parser.parse_args()
    root = args.directory.resolve()
    report = json.loads((root / "validation.json").read_text())
    validate(root, report)
    if args.verify_only:
        print("Evidence hashes and declared validation gates passed")
        return
    if args.archive.exists() or args.archive.resolve().is_relative_to(root):
        parser.error("Use a new archive path outside the delivery directory")
    references = [entry["reference"] for entry in report["images"]]
    expected_arch = report.get(
        "architecture", "arm64" if report["platform"] == "ascend" else "amd64"
    )
    inspect, platform = inspect_images(references, "linux/" + expected_arch)
    if len(inspect) != len(report["images"]):
        raise ValueError("Image inventory count mismatch")
    for entry, actual in zip(report["images"], inspect):
        if entry["id"] != actual["Id"]:
            raise ValueError(f"Image changed after validation: {entry['reference']}")
        expected_arch = report.get(
            "architecture", "arm64" if report["platform"] == "ascend" else "amd64"
        )
        if actual["Architecture"] != expected_arch:
            raise ValueError(f"Wrong architecture: {entry['reference']}")
    (root / "image-inspect.json").write_text(json.dumps(inspect, indent=2) + "\n")
    images = root / "images.tar.gz"
    if args.prepared_export:
        export = json.loads((root / "image-export.json").read_text())
        expected = {entry["reference"]: entry["id"] for entry in report["images"]}
        actual = {entry["reference"]: entry["id"] for entry in export["images"]}
        if expected != actual or export["archive"] != "images.tar.gz":
            raise ValueError("Prepared export does not match the validated images")
        if sha256(images) != export["sha256"]:
            raise ValueError("Prepared image archive changed")
    else:
        if images.exists():
            raise ValueError(
                "Existing images archive; do not overwrite a sealed export"
            )
        temporary = images.with_suffix(".gz.partial")
        with temporary.open("wb") as target:
            save = subprocess.Popen(
                save_command(references, platform), stdout=subprocess.PIPE
            )
            compress = subprocess.Popen(
                ["gzip", "-1"], stdin=save.stdout, stdout=target
            )
            save.stdout.close()
            compressed = compress.wait()
            exported = save.wait()
        if compressed or exported:
            raise RuntimeError(
                f"Image export failed: docker={exported}, gzip={compressed}"
            )
        temporary.rename(images)
    # A successful import into a populated daemon could hide omitted layers.
    # Check every OCI descriptor and byte hash before sealing the offline export.
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("verify-images.py")),
            str(images),
            "--output",
            str(root / "image-archive-verification.json"),
        ],
        check=True,
    )
    shutil.copy2(Path(__file__).with_name("import.sh"), root / "import.sh")
    files = sorted(
        p for p in root.rglob("*") if p.is_file() and p != root / "SHA256SUMS"
    )
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("Delivery must be self-contained, with no symlinks")
    (root / "SHA256SUMS").write_text(
        "".join(f"{sha256(p)}  {p.relative_to(root)}\n" for p in files)
    )
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=root, check=True)
    partial = args.archive.with_suffix(args.archive.suffix + ".partial")
    with tarfile.open(partial, "w") as archive:
        archive.add(root, arcname=root.name)
    partial.rename(args.archive)
    args.archive.with_suffix(args.archive.suffix + ".sha256").write_text(
        f"{sha256(args.archive)}  {args.archive.name}\n"
    )
    print(args.archive)


if __name__ == "__main__":
    main()
