#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check Profiler candidate receipts and aggregate export integrity.

This is a functional Profiler regression check, not an optimization benchmark
audit or a cross-platform performance comparison. Full per-request records are
retained in the archive; warmup and profiling phase exports are not added twice.
"""
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--directory", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
candidates = []
for receipt in sorted(a.directory.glob("*/profile-identity.json")):
    d = json.loads(receipt.read_text())
    mismatches = []
    for name, digest in d["files"].items():
        f = receipt.parent / name
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            mismatches.append(name)
    exports = []
    for f in sorted(receipt.parent.glob("*/profile_export_aiperf.json")):
        record = json.loads(f.read_text())
        count = record.get("completed_request_count", {}).get("avg", 0)
        errors = record.get("error_summary")
        valid = (
            record.get("is_complete") is True
            and record.get("was_cancelled") is False
            and errors == []
            and count > 0
            and record.get("request_error_rate", {}).get("avg", 0) == 0
        )
        exports.append(
            dict(
                file=str(f.relative_to(a.directory)),
                aiperf_version=record.get("aiperf_version"),
                completed=count,
                errors=errors,
                passed=valid,
                input_tokens=record.get("input_sequence_length"),
                output_tokens=record.get("output_sequence_length"),
            )
        )
    candidates.append(
        dict(
            candidate=receipt.parent.name,
            identity=d["identity"],
            manifest_kind=d["kind"],
            hash_mismatches=mismatches,
            exports=exports,
            passed=not mismatches
            and bool(exports)
            and all(x["passed"] for x in exports),
        )
    )
report = dict(
    scope="Profiler functional regression; no performance-optimality assertion",
    candidate_count=len(candidates),
    candidates=candidates,
    passed=len(candidates) == 4 and all(c["passed"] for c in candidates),
)
a.output.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({k: v for k, v in report.items() if k != "candidates"}))
raise SystemExit(0 if report["passed"] else 1)
