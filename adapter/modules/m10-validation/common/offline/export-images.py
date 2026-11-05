#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Export immutable image candidates before validation, without sealing a release."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from bundle import sha256
from image_helpers import inspect_images, save_command

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", type=Path, required=True)
p.add_argument("references", nargs="+")
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
archive = a.output / "images.tar.gz"
if archive.exists() or (a.output / "image-export.json").exists():
    p.error("Use a fresh export directory; existing exports are immutable")
inspect, platform = inspect_images(a.references)
assert len(inspect) == len(a.references)
partial = archive.with_suffix(".gz.partial")
with partial.open("wb") as out:
    save = subprocess.Popen(
        save_command(a.references, platform), stdout=subprocess.PIPE
    )
    compress = subprocess.Popen(["gzip", "-1"], stdin=save.stdout, stdout=out)
    save.stdout.close()
    compressed, saved = compress.wait(), save.wait()
if compressed or saved:
    raise RuntimeError(f"Export failed: docker={saved} gzip={compressed}")
after, _ = inspect_images(a.references, platform)
if [v["Id"] for v in inspect] != [v["Id"] for v in after]:
    raise RuntimeError("Image tags changed during export")
partial.rename(archive)
subprocess.run(
    [
        sys.executable,
        str(Path(__file__).with_name("verify-images.py")),
        str(archive),
        "--output",
        str(a.output / "image-archive-verification.json"),
    ],
    check=True,
)
record = {
    "archive": archive.name,
    "sha256": sha256(archive),
    "images": [
        {"reference": ref, "id": item["Id"], "architecture": item["Architecture"]}
        for ref, item in zip(a.references, inspect)
    ],
}
(a.output / "image-export.json").write_text(json.dumps(record, indent=2) + "\n")
print("Complete image export; release validation is still required", flush=True)
