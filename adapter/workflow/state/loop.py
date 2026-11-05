# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Durable bounded implement/freeze/execute/diagnose loop; qualification stays deterministic."""

import json
import time

from adapter.workflow.core import (
    atomic,
    digest,
    init_campaign,
    load_campaign,
    lock,
    now,
    read,
)
from adapter.workflow.harnesses.agent import handoff
from adapter.workflow.preflight import doctor
from adapter.workflow.scope import accepted
from adapter.workflow.state.compile import write_plan
from adapter.workflow.state.runner import run


def diagnose(path, state, key):
    item = state["features"][key]
    if item.get("reconciliation_required") or item["task"] == "running":
        return {
            "category": "recovery_required",
            "reason": "Observe owned resources before retry",
        }
    if not item["attempts"]:
        return {"category": "environment_or_plan", "reason": item.get("reason", [])}
    attempt = path / item["attempts"][-1]["path"]
    for observation in attempt.glob("cases/*/observations.json"):
        data = read(observation)
        if data.get("failure_classification") == "native_engine":
            paired = data.get("native_control_artifact")
            file = (attempt / paired["path"]).resolve() if paired else None
            if (
                not file
                or not file.is_relative_to(attempt)
                or not file.is_file()
                or digest(file) != paired["sha256"]
            ):
                return {
                    "category": "inconclusive",
                    "reason": "Native attribution lacks paired evidence",
                }
            return {
                "category": "native_engine",
                "reason": "Preserve paired native failure; do not change adaptation to hide it",
                "evidence": str(observation),
            }
    return {"category": "adaptation_or_recipe", "reason": item.get("reason", [])}


def automate(campaign, harness, model, max_cycles=3, wall_seconds=1800, agent=handoff):
    if not model or max_cycles < 1 or max_cycles > 20 or wall_seconds < 1:
        raise ValueError("Explicit model and bounded cycles/time required")
    origin, original, _ = load_campaign(campaign)
    if not original["environment"].get("execution_enabled"):
        raise ValueError("Execution disabled; use handoff for planning")
    with lock(origin / ".loop.lock"):
        file = origin / "loop.json"
        state = (
            read(file)
            if file.exists()
            else {
                "started": now(),
                "campaign": str(origin),
                "cycles": [],
                "agent_calls": 0,
                "elapsed_seconds": 0,
                "limits": {"max_cycles": max_cycles, "wall_seconds": wall_seconds},
            }
        )
        if state["limits"] != {"max_cycles": max_cycles, "wall_seconds": wall_seconds}:
            raise ValueError(
                "Loop budgets are immutable; resume with the original limits"
            )
        if state.get("inflight"):
            raise ValueError(
                "Interrupted AI/run cycle: inspect loop evidence and owned resources; do not blindly replay"
            )
        started = time.monotonic()
        try:
            while len(state["cycles"]) < max_cycles:
                remaining = (
                    wall_seconds
                    - state["elapsed_seconds"]
                    - (time.monotonic() - started)
                )
                path, frozen, current = load_campaign(state["campaign"])
                if all(accepted(current["features"][k]) for k in frozen["required"]):
                    state["status"] = "qualified_declared_scope"
                    break
                if (
                    remaining
                    < 3 * frozen["environment"]["budgets"]["task_timeout_seconds"]
                ):
                    state["status"] = "budget_exhausted"
                    break
                key = next(
                    k for k in frozen["planned"] if not accepted(current["features"][k])
                )
                diagnosis = diagnose(path, current, key)
                if diagnosis["category"] in {
                    "native_engine",
                    "inconclusive",
                    "recovery_required",
                }:
                    state["status"] = diagnosis["category"]
                    state["diagnosis"] = diagnosis
                    break
                cycle = {
                    "feature_id": key,
                    "harness": harness,
                    "model": model,
                    "diagnosis": diagnosis,
                    "started": now(),
                }
                state["inflight"] = cycle
                state["agent_calls"] += 1
                atomic(file, state)
                output = agent(path, key, harness, model, True)
                proposal = read(output["result"])
                cycle["proposal"] = output["result"]
                if proposal["status"] == "blocked":
                    cycle["blockers"] = proposal["blockers"]
                    state["status"] = "agent_blocked"
                    state["cycles"].append(cycle)
                    state.pop("inflight")
                    break
                # Every implementation iteration creates a new immutable campaign.
                target = origin.with_name(
                    origin.name + "-cycle-" + str(len(state["cycles"]) + 1)
                )
                env_path = path / "lock-environment.json"
                atomic(env_path, frozen["environment"])
                init_campaign(
                    target,
                    frozen["platform"],
                    frozen["chip"],
                    env_path,
                    scope=frozen["scope"],
                )
                state["campaign"] = str(target)
                atomic(
                    target / "parent.json",
                    {
                        "campaign": str(path),
                        "lock_sha256": digest(path / "lock.json"),
                        "proposal": output["result"],
                    },
                )
                # Inherit only demonstrably equivalent accepted inputs, revalidate their receipts.
                from adapter.workflow.evidence.inheritance import inherit

                for dep in frozen["planned"]:
                    if accepted(current["features"][dep]):
                        try:
                            inherit(
                                target,
                                path,
                                dep,
                                "workflow-equivalence-check",
                                "Exact inputs and original receipts verified",
                            )
                        except ValueError:
                            pass  # Changed inputs must execute again, never force an inheritance.
                recipes = (
                    read(path / "recipes.json")
                    if (path / "recipes.json").exists()
                    else {}
                )
                recipes.update(json.loads(proposal["tasks_json"]))
                atomic(target / "recipes.json", recipes)
                planning = write_plan(target, recipes)
                cycle["planning"] = planning
                preflight = doctor(target)
                if (
                    any(x["required"] for x in planning["missing"])
                    or not preflight["passed"]
                ):
                    state["status"] = "environment_or_plan_blocked"
                else:
                    cycle["result"] = run(target, key)
                    state["status"] = (
                        "qualified_declared_scope"
                        if all(
                            accepted(cycle["result"]["features"][k])
                            for k in frozen["required"]
                        )
                        else "continue"
                    )
                state["cycles"].append(cycle)
                state.pop("inflight")
                atomic(file, state)
                if state["status"] != "continue":
                    break
            else:
                state["status"] = "budget_exhausted"
        finally:
            state["elapsed_seconds"] += time.monotonic() - started
            atomic(file, state)
    return state
