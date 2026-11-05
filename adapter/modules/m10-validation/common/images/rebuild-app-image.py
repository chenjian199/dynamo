#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rebuild a runtime with an audited Python application wheel and unchanged native artifacts."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--original", type=Path, required=True)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--wheel", type=Path, required=True)
p.add_argument("--patch", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--tag", required=True)
p.add_argument(
    "--refresh-vendor-patches",
    action="store_true",
    help="Copy the hash-guarded M05 patch installer and patches from the audited source",
)
p.add_argument(
    "--changes",
    type=Path,
    required=True,
    help="Expected changed source paths and SHA256 values",
)
a = p.parse_args()
old = a.original / "image-context"
art = old / "artifacts"
for name, sha in json.loads((art / "SHA256.json").read_text()).items():
    if digest(art / name) != sha:
        raise ValueError("Original artifact changed: " + name)
source = json.loads((art / "SOURCE.json").read_text())
expected_changes = json.loads(a.changes.read_text())
changes = {
    name: digest(a.source / name)
    for name in source["files"].keys() | expected_changes.keys()
    if digest(a.source / name) != source["files"].get(name)
}
if changes != expected_changes:
    raise ValueError(f"Unexpected source changes: {changes}")
a.output.mkdir(parents=True, exist_ok=False)
context = a.output / "image-context"
shutil.copytree(old, context)
art = context / "artifacts"
old_wheels = list((art / "wheels").glob("ai_dynamo-*.whl"))
if len(old_wheels) != 1 or not a.wheel.name.startswith("ai_dynamo-"):
    raise ValueError("Expected exactly one Python application wheel")
previous_wheel = {"name": old_wheels[0].name, "sha256": digest(old_wheels[0])}
old_wheels[0].unlink()
shutil.copy2(a.wheel, art / "wheels" / a.wheel.name)
if a.refresh_vendor_patches:
    vendor_source = a.source / "adapter/modules/m05-pd-transfer/common/transfer"
    for name in ("patch_vendor.py", "patch_ascend_tp.py"):
        shutil.copy2(vendor_source / name, art / "vendor-patches" / name)
    shutil.copytree(
        vendor_source / "patches",
        art / "vendor-patches/patches",
        dirs_exist_ok=True,
    )
source["files"].update(changes)
source["previous_patch_sha256"] = source.get("patch_sha256")
source["patch_sha256"] = digest(a.patch)
(art / "SOURCE.json").write_text(json.dumps(source, indent=2) + "\n")
record = {
    "base_image_record": json.loads((a.original / "image.json").read_text()),
    "source_changes": changes,
    "patch_sha256": digest(a.patch),
    "original_wheel": previous_wheel,
    "replacement_wheel": {"name": a.wheel.name, "sha256": digest(a.wheel)},
    "scope": "Python application wheel; original Rust binding, dependencies, native KVBM artifacts and version locks retained",
    "vendor_patches_refreshed": a.refresh_vendor_patches,
}
(art / "REBUILD.json").write_text(json.dumps(record, indent=2) + "\n")
shutil.copy2(a.patch, art / "application.patch")
manifest = {
    str(f.relative_to(art)): digest(f)
    for f in sorted(art.rglob("*"))
    if f.is_file() and f.name != "SHA256.json"
}
(art / "SHA256.json").write_text(json.dumps(manifest, indent=2) + "\n")
profile = json.loads((art / "profile.json").read_text())
subprocess.run(
    [
        "docker",
        "build",
        "--network",
        "none",
        "--build-arg",
        "BASE_IMAGE=" + profile["base_image"],
        "--build-arg",
        "ACCELERATOR=" + profile["id"],
        "-f",
        str(context / "runtime.Dockerfile"),
        "-t",
        a.tag,
        str(context),
    ],
    check=True,
)
image = json.loads(subprocess.check_output(["docker", "inspect", a.tag]))[0]
(a.output / "image.json").write_text(
    json.dumps(
        {
            "id": image["Id"],
            "tag": a.tag,
            "profile": profile,
            "source_commit": source["commit"],
            "source_patch_sha256": source["patch_sha256"],
        },
        indent=2,
    )
    + "\n"
)
print(a.output / "image.json")
