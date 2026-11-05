#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Audit functional sampling completeness and select original sweep metadata.

This does not certify production SLOs or compare benchmark performance.
Raw exports and snapshots are never modified.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtimes(value):
    if isinstance(value, dict):
        return int("runtime_config" in value) + sum(
            runtimes(child) for child in value.values()
        )
    if isinstance(value, list):
        return sum(runtimes(child) for child in value)
    return 0


def audit_runs(profile):
    runs = []
    for path in sorted(profile.rglob("profile_export_aiperf.json")):
        data = json.loads(path.read_text())
        phase = path.parent.name if path.parent.parent.name == "phases" else "profiling"
        expected = sum(
            item.get("requests", 0)
            for item in data["input_config"]["phases"]
            if item["kind"] == phase
        )
        actual = data.get("completed_request_count", data.get("request_count", {})).get(
            "avg"
        )
        runs.append(
            {
                "file": str(path.relative_to(profile)),
                "sha256": digest(path),
                "phase": phase,
                "aiperf_version": data["aiperf_version"],
                "expected_requests": expected,
                "completed_requests": actual,
                "is_complete": data["is_complete"],
                "was_cancelled": data["was_cancelled"],
                "error_summary": data["error_summary"],
                "input_sequence_length": data.get("input_sequence_length"),
                "output_sequence_length": data.get("output_sequence_length"),
                "valid_execution": bool(
                    expected > 0
                    and expected == actual
                    and data["is_complete"]
                    and not data["was_cancelled"]
                    and not data["error_summary"]
                ),
            }
        )
    return runs


def audit(profile, snapshots, log, output):
    output.mkdir(parents=True, exist_ok=False)
    runs = audit_runs(profile)
    names = re.findall(r"Successfully created deployment ([\w-]+)", log.read_text())
    if len(names) != 2:
        raise ValueError(
            f"Expected separate prefill/decode sweep deployments, got {names}"
        )
    selected = {}
    for role, name in zip(("prefill", "decode"), names):
        for path in sorted(snapshots.glob("*.mdc.json")):
            data = json.loads(path.read_text())
            items = [
                item
                for item in data["items"]
                if item["metadata"]["name"].startswith(name + "-") and runtimes(item)
            ]
            if runtimes(items) != 1:
                continue
            target = output / f"{role}-metadata.json"
            target.write_text(json.dumps({"items": items}, indent=2) + "\n")
            selected[role] = {
                "deployment": name,
                "snapshot": path.name,
                "snapshot_sha256": digest(path),
                "selected_sha256": digest(target),
            }
            break
        if role not in selected:
            raise ValueError(f"No single-engine original metadata for {role}: {name}")
    result = {
        "scope": "Profiler execution completeness; not a production SLO benchmark",
        "runs": runs,
        "metadata": selected,
        "passed": bool(runs) and all(run["valid_execution"] for run in runs),
    }
    (output / "execution.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def audit_dgdr(profile, output):
    import yaml

    output.mkdir(parents=True, exist_ok=False)
    runs = audit_runs(profile)
    expected = {
        "prefill_1gpus_tp1",
        "prefill_2gpus_tp2",
        "decode_1gpus_tp1",
        "decode_2gpus_tp2",
    }
    actual = {Path(run["file"]).parts[0] for run in runs}
    status = yaml.safe_load((profile / "profiler_status.yaml").read_text())
    configs = list(yaml.safe_load_all((profile / "final_config.yaml").read_text()))
    result = {
        "scope": "DGDR thorough TP1/TP2 functional regression; no optimality or SLA claim",
        "expected_candidates": sorted(expected),
        "actual_candidates": sorted(actual),
        "profile_identity_receipts": "Not requested by this DGDR; offline bootstrap is audited separately",
        "final_config_sha256": digest(profile / "final_config.yaml"),
        "runs": runs,
        "passed": bool(runs)
        and actual == expected
        and status.get("status") == "success"
        and any(obj and obj.get("kind") == "DynamoGraphDeployment" for obj in configs)
        and all(run["valid_execution"] for run in runs),
    }
    (output / "execution.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dgdr", action="store_true")
    for name in ("profile", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("snapshots", "log"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.dgdr:
        result = audit_dgdr(args.profile, args.output)
    else:
        if not args.snapshots or not args.log:
            parser.error("Offline sweeps require --snapshots and --log")
        result = audit(args.profile, args.snapshots, args.log, args.output)
    print(json.dumps({"passed": result["passed"], "runs": len(result["runs"])}))
    raise SystemExit(0 if result["passed"] else 1)
