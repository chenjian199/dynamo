#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Archive one finished offline campaign and export its profile/Planner evidence."""
import argparse
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--workdir", type=Path, required=True)
p.add_argument("--profile", required=True)
p.add_argument("--online", default="online-final")
p.add_argument("--restart", default="restart-final")
a = p.parse_args()
out = a.workdir / "offline-export"
out.mkdir(exist_ok=True)
archive_only = {"server_metrics.json", "server_metrics_export.json"}
for stale in out.rglob("*"):
    if stale.is_file() and stale.name in archive_only:
        stale.unlink()
roots = [a.workdir / a.profile, a.workdir / a.online, a.workdir / a.restart]
roots += [
    f
    for f in a.workdir.iterdir()
    if f.is_file() and f.suffix in {".json", ".yaml", ".log"}
]
archive = a.workdir / "offline-evidence.tar.gz"
with tarfile.open(archive, "w:gz") as tar:
    for root in roots:
        if root.exists():
            tar.add(root, arcname=root.relative_to(a.workdir))
for root in roots:
    files = [root] if root.is_file() else root.rglob("*")
    for f in files:
        if not f.is_file() or f.is_symlink() or f.name in archive_only:
            continue
        target = out / f.relative_to(a.workdir)
        if f.suffix == ".log" and f.name != "load.log":
            target = target.with_suffix(".selected.log")
            target.parent.mkdir(parents=True, exist_ok=True)
            lines = f.read_text(errors="replace").splitlines()
            target.write_text(
                "\n".join(
                    line
                    for line in lines
                    if re.search(
                        r"ERROR|Traceback|Offline bootstrap|Bootstrapped|Prefill:|Decode:|\[summary\]|replica|budget|SOURCE|COMPLETE|failed|Failure|PlatformError",
                        line,
                    )
                )
                + "\n"
            )
        # Full worker logs, plots, CSVs and per-request AIPerf records stay in
        # the hashed remote archive; preserve aggregate exports and raw NPZ.
        if f.suffix not in {".json", ".yaml", ".npz"} and f.name not in {
            "scaling.jsonl",
            "before.log",
            "after.log",
            "load.log",
            "restart-smoke.log",
            "profile-v1.log",
            "profile-v3.log",
        }:
            continue
        target = out / f.relative_to(a.workdir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, target)
source_audits = []
for source in a.workdir.glob("source*/SOURCE.json"):
    shutil.copyfile(source, out / (source.parent.name + ".json"))
    expected = json.loads(source.read_text())["files"]
    mismatches = [
        name
        for name, digest in expected.items()
        if not (source.parent / name).is_file()
        or hashlib.sha256((source.parent / name).read_bytes()).hexdigest() != digest
    ]
    source_audits.append({"source": source.parent.name, "modified_files": mismatches})
(out / "source-snapshot-audit.json").write_text(
    json.dumps(source_audits, indent=2) + "\n"
)
audits = []
for f in (a.workdir / a.profile).rglob("profile_export_aiperf.json"):
    d = json.loads(f.read_text())
    phases = d["input_config"]["phases"]
    phase = f.parent.name if f.parent.parent.name == "phases" else "profiling"
    expected = sum(x.get("requests", 0) for x in phases if x["kind"] == phase)
    actual = d.get("completed_request_count", d.get("request_count", {})).get("avg")
    audits.append(
        {
            "file": str(f.relative_to(a.workdir)),
            "phase": phase,
            "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
            "aiperf_version": d["aiperf_version"],
            "expected_requests": expected,
            "completed_requests": actual,
            "is_complete": d["is_complete"],
            "was_cancelled": d["was_cancelled"],
            "error_summary": d["error_summary"],
            "output_sequence_length": d.get("output_sequence_length"),
            "usage_completion_tokens": d.get("usage_completion_tokens"),
            "valid_execution": bool(
                d["is_complete"]
                and not d["was_cancelled"]
                and not d["error_summary"]
                and expected == actual
            ),
        }
    )
(out / "profile-execution-audit.json").write_text(
    json.dumps(
        {
            "scope": "execution completeness; not production SLA or model quality certification",
            "runs": audits,
            "passed": bool(audits) and all(x["valid_execution"] for x in audits),
        },
        indent=2,
    )
    + "\n"
)
digest = hashlib.sha256()
with archive.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
(out / "archive.json").write_text(
    json.dumps(
        {
            "path": str(archive),
            "sha256": digest.hexdigest(),
            "bytes": archive.stat().st_size,
        },
        indent=2,
    )
    + "\n"
)
print(out)
