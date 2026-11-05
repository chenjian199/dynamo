#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rebuild a runtime with one audited Rust-binding fix and unchanged other artifacts."""
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
a = p.parse_args()
old = a.original / "image-context"
art = old / "artifacts"
for name, sha in json.loads((art / "SHA256.json").read_text()).items():
    if digest(art / name) != sha:
        raise ValueError("Original artifact changed: " + name)
source = json.loads((art / "SOURCE.json").read_text())
changes = {
    name: digest(a.source / name)
    for name, sha in source["files"].items()
    if digest(a.source / name) != sha
}
if set(changes) != {"lib/bindings/python/rust/llm/fpm.rs"}:
    raise ValueError(f"Unexpected source changes: {changes}")
a.output.mkdir(parents=True, exist_ok=False)
context = a.output / "image-context"
shutil.copytree(old, context)
art = context / "artifacts"
old_wheels = list((art / "wheels").glob("ai_dynamo_runtime-*.whl"))
if len(old_wheels) != 1 or not a.wheel.name.startswith("ai_dynamo_runtime-"):
    raise ValueError("Expected exactly one runtime binding wheel")
previous_wheel = {"name": old_wheels[0].name, "sha256": digest(old_wheels[0])}
old_wheels[0].unlink()
shutil.copy2(a.wheel, art / "wheels" / a.wheel.name)
source["files"].update(changes)
source["patch_sha256"] = digest(a.patch)
(art / "SOURCE.json").write_text(json.dumps(source, indent=2) + "\n")
record = {
    "base_image_record": json.loads((a.original / "image.json").read_text()),
    "source_changes": changes,
    "patch_sha256": digest(a.patch),
    "original_wheel": previous_wheel,
    "replacement_wheel": {"name": a.wheel.name, "sha256": digest(a.wheel)},
    "scope": "Rust runtime binding only; original dependency wheels, native KVBM artifacts, vendor patches and version locks retained",
}
(art / "REBUILD.json").write_text(json.dumps(record, indent=2) + "\n")
shutil.copy2(a.patch, art / "fpm-gil.patch")
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
