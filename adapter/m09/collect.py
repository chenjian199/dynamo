#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Retain full remote evidence archives and export compact, auditable results."""
import argparse
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--workdir", type=Path, required=True)
p.add_argument("--run", required=True)
a = p.parse_args()
source = a.workdir / a.run
out = a.workdir / "export" / a.run
out.mkdir(parents=True, exist_ok=True)
archives = a.workdir / "archives"
archives.mkdir(exist_ok=True)
archive = archives / (a.run + ".tar.gz")
with tarfile.open(archive, "w:gz") as tar:
    tar.add(source, arcname=a.run)
for name in [
    "result.json",
    "contract.json",
    "scaling.jsonl",
    "load.log",
    "latest-dgd.json",
    "latest-pods.json",
]:
    if (source / name).is_file():
        shutil.copyfile(source / name, out / name)
for path in source.glob("*.log"):
    if path.name == "load.log":
        continue
    lines = path.read_text(errors="replace").splitlines()
    selected = [
        line
        for line in lines
        if re.search(
            r"ERROR|Traceback|EngineShutdown|threshold|\[summary\]|Updating.*replica|source=.*readiness|graceful_shutdown|Traceback|ModuleNotFound|PermissionError",
            line,
        )
    ]
    (out / (path.stem + ".selected.log")).write_text("\n".join(selected) + "\n")
records = []
for line in (
    (source / "load.log").read_text().splitlines()
    if (source / "load.log").exists()
    else []
):
    if line.startswith("RESULT="):
        records.append(json.loads(line[7:]))
if records:
    (out / "latency.json").write_text(json.dumps(records[-1], indent=2) + "\n")
receipt = dict(
    archive=str(archive),
    sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
    bytes=archive.stat().st_size,
    source=str(source),
    exported_files=sorted(x.name for x in out.iterdir()),
)
(out / "archive.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt))
