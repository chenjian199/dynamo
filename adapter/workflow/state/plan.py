# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate and freeze executable tasks separately from an AI's proposed plan."""

from pathlib import Path

import jsonschema

from adapter.workflow.core import (
    ADAPTER,
    ROOT,
    atomic,
    event,
    fingerprint,
    load_campaign,
    lock,
    now,
    read,
)
from adapter.workflow.executors.commands import command, substitute


def validate_plan(plan, frozen):
    try:
        jsonschema.validate(
            plan, read(ADAPTER / "contracts/schemas/task-plan.schema.json")
        )
    except jsonschema.ValidationError as exc:
        raise ValueError("Invalid task plan: " + exc.message) from exc
    tasks = {t["feature_id"]: t for t in plan["tasks"]}
    if len(tasks) != len(plan["tasks"]):
        raise ValueError("Duplicate task for a feature")
    if set(tasks) - set(frozen["features"]):
        raise ValueError("Task outside frozen scope")
    if len(tasks) > frozen["environment"]["budgets"]["max_tasks"]:
        raise ValueError("Task budget exceeded")
    for task in tasks.values():
        feature = frozen["features"][task["feature_id"]]
        if task.get("case_ids") is not None:
            if set(task["case_ids"]) != {c["id"] for c in feature["cases"]}:
                raise ValueError("Compiled case coverage differs from frozen feature")
            for case_id, checksum in task.get("bindings_sha256", {}).items():
                if (
                    fingerprint(
                        frozen["environment"].get("case_bindings", {}).get(case_id)
                    )
                    != checksum
                ):
                    raise ValueError("Compiled case binding changed")
            from adapter.workflow.core import digest

            for name, checksum in task.get("recipe_sources", {}).items():
                if (
                    not (ROOT / name).resolve().is_relative_to(ROOT)
                    or digest(ROOT / name) != checksum
                ):
                    raise ValueError("Recipe entrypoint changed")
        for phase in ("prepare", "run", "observe", "cleanup", "verify"):
            for spec in task.get(phase, []):
                if (
                    spec.get("optional")
                    and feature.get("case_scope", {})
                    .get(spec.get("case_id"), {})
                    .get("disposition")
                    != "optional"
                ):
                    raise ValueError(
                        "Cannot mark a required/unknown case command optional"
                    )
                try:
                    expanded = substitute(
                        spec,
                        frozen["environment"],
                        ROOT,
                        Path("/validation/features")
                        / task["feature_id"]
                        / "attempt-000",
                        frozen["identity"],
                    )
                    command(expanded, frozen["environment"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{task['feature_id']}/{phase}: invalid executor: {exc}"
                    ) from exc
        # Verification reads collected artifacts on the coordinator. Remote test
        # runners must collect first, not write a verdict on an unseen filesystem.
        if any(s.get("executor", "local") != "local" for s in task["verify"]):
            raise ValueError(
                "Verification must run locally against collected attempt evidence"
            )
    return tasks


def sealed_plan(path, frozen, *, create=False):
    """Caller owns campaign lock. A changed plan always requires a new campaign."""
    plan = read(path / "tasks.json")
    validate_plan(plan, frozen)
    seal_path = path / "plan-lock.json"
    if seal_path.exists():
        seal = read(seal_path)
        if (
            seal.get("identity") != frozen["identity"]
            or seal.get("plan_sha256") != fingerprint(plan)
            or seal.get("plan") != plan
        ):
            raise ValueError(
                "Task plan changed after sealing; initialize a new campaign"
            )
    elif create:
        seal = {
            "schema_version": 1,
            "identity": frozen["identity"],
            "plan_sha256": fingerprint(plan),
            "plan": plan,
            "sealed_at": now(),
        }
        atomic(seal_path, seal)
        event(path, "plan_sealed", plan_sha256=seal["plan_sha256"])
    else:
        raise ValueError("Task plan is not sealed")
    return seal


def seal_plan(campaign):
    path, _, _ = load_campaign(campaign)
    with lock(path / ".lock"):
        path, frozen, _ = load_campaign(path)
        return sealed_plan(path, frozen, create=True)
