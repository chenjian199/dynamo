# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Upgrade comparison and offline delivery gates, with byte-level evidence provenance."""

import math
from pathlib import Path

from adapter.workflow.core import atomic, digest, fingerprint, lock, read
from adapter.workflow.delivery import PHASES, validate_smoke
from adapter.workflow.upgrade.session import (
    attach_campaign,
    candidate,
    load,
    save,
    status,
)


def compare(session, platform, input_file):
    with lock(Path(session) / ".lock"):
        path, frozen, state, _ = load(session)
        _, head = candidate(frozen, state)
        source = Path(input_file).resolve()
        if not source.is_relative_to(path):
            raise ValueError(
                "Comparison inputs and raw artifacts must be under this upgrade result directory"
            )
        data = read(source)
        runs = data["runs"]
        if set(runs) != {"old_heyi", "new_heyi", "native"}:
            raise ValueError("Require old HEYI, new HEYI and native engine controls")
        expected = {
            "old_heyi": frozen["commits"]["heyi"],
            "new_heyi": head,
            "native": None,
        }
        versions = {
            "old_heyi": frozen["stacks"][platform]["versions"]["vllm"],
            "new_heyi": state["images"][platform]["versions"]["vllm"],
            "native": state["images"][platform]["versions"]["vllm"],
        }
        artifacts, series = {str(source): digest(source)}, []
        for role, run in runs.items():
            if run["source_commit"] != expected[role] or run["vllm"] != versions[role]:
                raise ValueError("Wrong source/version in " + role)
            for field in [
                "workload_sha256",
                "weights_sha256",
                "hardware",
                "parallelism",
                "load",
                "sampling",
                "metric_definition",
            ]:
                if not run["conditions"].get(field):
                    raise ValueError("Missing comparable conditions: " + field)
            series.append(fingerprint(run["conditions"]))
            if not run.get("artifacts"):
                raise ValueError("Raw performance/correctness artifacts required")
            for entry in run["artifacts"]:
                file = (source.parent / entry["path"]).resolve()
                if (
                    not file.is_relative_to(path)
                    or not file.is_file()
                    or digest(file) != entry["sha256"]
                ):
                    raise ValueError("Missing/changed/outside raw comparison artifact")
                artifacts[str(file)] = entry["sha256"]
            if not run["correct"] or run["failed_requests"] != 0:
                raise ValueError(
                    "Correctness/native-engine failure: diagnose before performance acceptance"
                )
            for metric in ["throughput", "ttft_p95_ms", "tpot_p95_ms"]:
                value = run["metrics"][metric]
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or value <= 0
                ):
                    raise ValueError("Metrics must be finite positive numbers")
        if len(set(series)) != 1:
            raise ValueError(
                "Unmatched model/workload/hardware/load; cannot attribute a performance change"
            )
        old, new, native = [
            runs[k]["metrics"] for k in ["old_heyi", "new_heyi", "native"]
        ]
        ratios = {k: new[k] / old[k] for k in old if k in new}
        limits = frozen["plan"]["comparison_limits"]
        passed = ratios["throughput"] >= limits["min_throughput_ratio"] and all(
            ratios[k] <= limits["max_latency_ratio"]
            for k in ["ttft_p95_ms", "tpot_p95_ms"]
        )
        record = {
            "commit": head,
            "status": "passed" if passed else "failed",
            "artifacts": artifacts,
            "new_over_old": ratios,
            "new_over_native": {k: new[k] / native[k] for k in native if k in new},
            "attribution": "Combined Dynamo + vendor-engine stack upgrade; not Dynamo-only performance attribution",
        }
        attempt = (
            path
            / "comparisons"
            / platform
            / f"{len(list((path / 'comparisons' / platform).glob('*.json'))) + 1:03}.json"
        )
        atomic(attempt, record)
        state["comparisons"][platform] = record
        save(path, state, "compared", platform=platform, status=record["status"])
        return record


def verify_delivery(file, campaign):
    file, campaign = Path(file).resolve(), Path(campaign).resolve()
    if not file.is_relative_to(campaign):
        raise ValueError("Delivery must be inside its qualified campaign")
    state, frozen = read(file), read(campaign / "lock.json")
    plan = state["plan"]
    if state["status"] != "artifacts_sealed" or state["identity"] != frozen["identity"]:
        raise ValueError("Delivery identity/status mismatch")
    if state["plan_sha256"] != fingerprint(plan) or state["lock_sha256"] != digest(
        campaign / "lock.json"
    ):
        raise ValueError("Changed delivery inputs")
    if list(state["phases"]) != list(PHASES):
        raise ValueError("All seven M02/M10 delivery phases are required")
    root = file.parent
    for phase in state["phases"].values():
        if phase["status"] != "completed" or not phase.get("artifacts"):
            raise ValueError("Delivery phase has no completed evidence")
        for name, checksum in phase["artifacts"].items():
            artifact = (root / name).resolve()
            if not artifact.is_relative_to(root) or digest(artifact) != checksum:
                raise ValueError("Changed delivery artifact")
    archive = (root / plan["archive"]).resolve()
    if not archive.is_relative_to(root) or digest(archive) != state["archive_sha256"]:
        raise ValueError("Archive checksum mismatch")
    validate_smoke(root, plan, frozen)
    return {
        "path": str(file),
        "sha256": digest(file),
        "archive": str(archive),
        "archive_sha256": state["archive_sha256"],
    }


def attach_delivery(session, platform, delivery_file):
    with lock(Path(session) / ".lock"):
        path, frozen, state, _ = load(session)
        _, head = candidate(frozen, state)
        campaign = state["campaigns"][platform]
        if campaign["commit"] != head:
            raise ValueError("Campaign is stale")
        result = dict(commit=head, **verify_delivery(delivery_file, campaign["path"]))
        state.setdefault("deliveries", {})[platform] = result
        save(path, state, "delivery_attached", platform=platform)
        return result


def promotion(session, rollback_file):
    # Re-audit every campaign at the final candidate, instead of trusting a previously green summary.
    path, frozen, state, _ = load(session)
    for platform in frozen["plan"]["platforms"]:
        if platform not in state["campaigns"]:
            raise ValueError("Missing qualified campaign: " + platform)
        attach_campaign(session, platform, state["campaigns"][platform]["path"])
    with lock(path / ".lock"):
        path, frozen, state, _ = load(session)
        result = status(session)
        if result["blockers"]:
            raise ValueError("Promotion blocked: " + ", ".join(result["blockers"]))
        for platform, record in state["deliveries"].items():
            verify_delivery(record["path"], state["campaigns"][platform]["path"])
        rollback = read(rollback_file)
        if rollback["source_commit"] != frozen["commits"]["heyi"] or not rollback.get(
            "reviewed_by"
        ):
            raise ValueError(
                "Rollback must identify the original HEYI commit and reviewer"
            )
        for field in [
            "image_restore",
            "crd_schema",
            "persistent_data",
            "traffic_switch",
            "verification",
        ]:
            if not rollback.get(field):
                raise ValueError("Rollback plan missing: " + field)
        output = path / "promotion.json"
        if output.exists():
            raise ValueError("Promotion proposal already sealed; never overwrite it")
        result.update(
            status="ready_for_human_promotion",
            rollback=rollback,
            frozen_commits=frozen["commits"],
            campaigns=state["campaigns"],
            deliveries=state["deliveries"],
            note="No branch, tag, remote or running service was changed. Publish and rollout require a separate action.",
        )
        atomic(output, result)
        return result
