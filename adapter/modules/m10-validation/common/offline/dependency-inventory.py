#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Inventory complete optional dependency archives and verify their architecture."""

import argparse
import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

from bundle import sha256

spec = importlib.util.spec_from_file_location(
    "image_archive_verifier", Path(__file__).with_name("verify-images.py")
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--directory", required=True, type=Path)
p.add_argument("--arch", choices=["amd64", "arm64"], required=True)
p.add_argument("--output", required=True, type=Path)
a = p.parse_args()
records = []
for path in sorted(a.directory.glob("*.tar")):
    integrity = verifier.verify(path)
    with tarfile.open(path) as archive:
        entries = json.load(archive.extractfile("manifest.json"))
        for entry in entries:
            raw = archive.extractfile(entry["Config"]).read()
            config = json.loads(raw)
            if config["architecture"] != a.arch or config["os"] != "linux":
                raise ValueError(f"Wrong platform: {path}: {entry['RepoTags']}")
            # Every named layer must exist: a transport-only delta is not an
            # offline dependency. Content SHA checks are also done by import.
            for layer in entry["Layers"]:
                archive.getmember(layer)
            records.append(
                {
                    "archive": path.name,
                    "archive_sha256": sha256(path),
                    "references": entry["RepoTags"],
                    "architecture": a.arch,
                    "config_sha256": hashlib.sha256(raw).hexdigest(),
                    "layer_count": len(entry["Layers"]),
                    "integrity": integrity,
                }
            )
if not records:
    p.error("No dependency image archives found")
a.output.write_text(json.dumps(records, indent=2) + "\n")
print(f"Verified {len(records)} dependency images for {a.arch}")
