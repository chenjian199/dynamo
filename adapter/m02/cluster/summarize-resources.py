#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Summarize sampled worker resources without treating missing native counters as PASS."""

import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--input", type=Path, nargs="+", required=True)
p.add_argument("--host")
p.add_argument(
    "--enforce-soak",
    action="store_true",
    help="Apply frozen process/native/idle gates to the complete window",
)
p.add_argument("--output", type=Path, required=True)
args = p.parse_args()
by_worker = {}
errors = []
for directory in args.input:
    for path in sorted(directory.glob("*.json")):
        for row in json.loads(path.read_text()):
            if args.host and row.get("host", args.host) != args.host:
                continue
            name = row.get("deployment", row.get("target"))
            if "snapshot" not in row:
                errors.append(
                    {"source": str(path), "deployment": name, "error": row.get("error")}
                )
                continue
            by_worker.setdefault(name, []).append(row["snapshot"])
report = {
    "qualification": "Process observations only; KV blocks and native transport handles are not measured.",
    "errors": errors,
    "workers": {},
}
for name, snapshots in by_worker.items():
    snapshots.sort(key=lambda s: s["time"])
    first, last = snapshots[0], snapshots[-1]

    def keys(s):
        return {(pid, proc["created"]) for pid, proc in s["processes"].items()}

    stable = all(keys(s) == keys(first) for s in snapshots)
    totals = [
        {
            key: sum(proc[key] for proc in s["processes"].values())
            for key in ("rss", "fds", "threads")
        }
        for s in snapshots
    ]
    deltas = {key: totals[-1][key] - totals[0][key] for key in totals[0]}
    report["workers"][name] = {
        "samples": len(snapshots),
        "start": first["time"],
        "end": last["time"],
        "seconds": last["time"] - first["time"],
        "stable_process_identity": stable,
        "max_sample_gap_seconds": max(
            (b["time"] - a["time"] for a, b in zip(snapshots, snapshots[1:])), default=0
        ),
        "first": totals[0],
        "last": totals[-1],
        "delta": deltas,
        "peak": {key: max(t[key] for t in totals) for key in totals[0]},
        "per_process_delta": {
            pid: {
                key: proc[key] - first["processes"][pid][key]
                for key in ("rss", "fds", "threads")
            }
            for pid, proc in last["processes"].items()
        }
        if stable
        else None,
    }
if args.enforce_soak:
    report["limits"] = {
        "rss_growth_bytes": 256 * 1024**2,
        "fd_growth": 8,
        "thread_growth": 4,
        "max_sample_gap_seconds": 90,
        "minimum_samples": 31,
    }
    report[
        "qualification"
    ] = "Post-warmup process/native handles and final engine occupancy; bounded window, not infinite leak freedom."
    for name, snapshots in by_worker.items():
        snapshots.sort(key=lambda s: s["time"])
        first, last = snapshots[0], snapshots[-1]
        row = report["workers"][name]
        failures = []
        if not row["stable_process_identity"]:
            failures.append("process identity changed")
        if row["seconds"] < 1800:
            failures.append("observation shorter than 1800 seconds")
        if row["samples"] < 31 or row["max_sample_gap_seconds"] > 90:
            failures.append("incomplete one-minute sampling window")
        for pid, delta in (row["per_process_delta"] or {}).items():
            if (
                delta["rss"] > 256 * 1024**2
                or delta["fds"] > 8
                or delta["threads"] > 4
            ):
                failures.append("process growth " + pid)
        initial_native = first.get("native", {})
        final_native = last.get("native", {})
        if not initial_native or set(initial_native) != set(final_native):
            failures.append("native observer missing or changed")
        for key, record in final_native.items():
            before = initial_native.get(key, {}).get("active", {})
            for category, value in record["active"].items():
                if category in ("transfers", "sync_calls"):
                    if value:
                        failures.append(f"{key}/{category} remains active")
                elif value > before.get(category, 0):
                    failures.append(f"{key}/{category} grew")
        occupancy = last.get("occupancy", [])
        if len(occupancy) != 3 or any(
            float(line.rsplit(" ", 1)[1]) != 0 for line in occupancy
        ):
            failures.append("final engine occupancy missing or nonzero")
        row.update(
            native_first=initial_native,
            native_last=final_native,
            final_occupancy=occupancy,
            failures=failures,
            passed=not failures,
        )
    report["passed"] = (
        bool(report["workers"])
        and not errors
        and all(r["passed"] for r in report["workers"].values())
    )
args.output.write_text(json.dumps(report, indent=2) + "\n")
args.output.with_name(args.output.name + ".license").write_text(
    "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\n"
)
print(json.dumps(report, indent=2))
