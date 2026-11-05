# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compile per-case recipes into a frozen task plan; no defaults for model or topology."""

import sys
from copy import deepcopy

from adapter.workflow.bindings import case_environment
from adapter.workflow.core import ROOT, atomic, digest, fingerprint, load_campaign, lock
from adapter.workflow.state.plan import validate_plan


def compile_plan(frozen, recipes=None):
    recipes = recipes or {}
    all_cases = {c["id"] for f in frozen["features"].values() for c in f["cases"]}
    if set(recipes) - all_cases:
        raise ValueError("Recipe outside frozen cases")
    tasks, missing = [], []
    for key in frozen["planned"]:
        feature = frozen["features"][key]
        task = {
            "feature_id": key,
            "prepare": [],
            "run": [],
            "observe": [],
            "cleanup": [],
            "resources": [],
            "case_ids": [],
            "recipe_sources": {},
            "bindings_sha256": {},
        }
        for case in feature["cases"]:
            try:
                env, binding = case_environment(frozen["environment"], feature, case)
                recipe = recipes.get(case["id"], binding.get("commands"))
                if (
                    not recipe
                    or not recipe.get("run")
                    or not recipe.get("observe")
                    or not recipe.get("cleanup")
                ):
                    raise ValueError(
                        "prepare/run/observe/cleanup recipe requires explicit run, observe and cleanup"
                    )
                values = {
                    "case_id": case["id"],
                    "platform": frozen["platform"],
                    "image": binding.get("image", ""),
                }
                values.update(
                    {k: str(v) for k, v in binding.get("topology", {}).items()}
                )
                values.update(
                    {
                        "model:" + m["id"]: m["path"]
                        for m in env["nodes"][0].get("models", [])
                    }
                )
                run_commands = []
                phases = {p: [] for p in ("prepare", "run", "observe", "cleanup")}
                for phase in ("prepare", "run", "observe", "cleanup"):
                    for command in recipe.get(phase, []):
                        spec = deepcopy(command)
                        spec["case_id"] = case["id"]
                        spec["optional"] = (
                            feature.get("case_scope", {})
                            .get(case["id"], {})
                            .get("disposition")
                            == "optional"
                        )
                        for name, value in values.items():
                            spec["argv"] = [
                                a.replace("{" + name + "}", value) for a in spec["argv"]
                            ]
                        if spec.get("node") and spec["node"] not in binding["nodes"]:
                            raise ValueError(
                                "Recipe executes on a node outside the case binding"
                            )
                        phases[phase].append(spec)
                        if phase == "run":
                            run_commands.extend(spec["argv"])
                entries = case["recipe"]["entrypoints"]
                if not any(
                    "{repo}/" + p in run_commands or str(ROOT / p) in run_commands
                    for p in entries
                ):
                    raise ValueError(
                        "Run must invoke a declared precise case entrypoint"
                    )
                for phase, commands in phases.items():
                    task[phase].extend(commands)
                task["case_ids"].append(case["id"])
                task["bindings_sha256"][case["id"]] = fingerprint(binding)
                task["recipe_sources"].update({p: digest(ROOT / p) for p in entries})
                task["resources"].extend(recipe.get("resources", []))
            except (ValueError, KeyError, OSError, TypeError) as exc:
                optional = (
                    feature.get("case_scope", {}).get(case["id"], {}).get("disposition")
                    == "optional"
                )
                missing.append(
                    {
                        "feature_id": key,
                        "case_id": case["id"],
                        "reason": str(exc),
                        "required": not optional,
                    }
                )
                if optional:
                    task["case_ids"].append(case["id"])
        task["verify"] = [
            {
                "argv": [
                    sys.executable,
                    "-m",
                    "adapter.workflow.evidence.verify",
                    "--envelope",
                    "{attempt}/envelope.json",
                ]
            }
        ]
        if len(task["case_ids"]) == len(feature["cases"]):
            tasks.append(task)
    plan = {"schema_version": 1, "tasks": tasks}
    validate_plan(plan, frozen)
    return plan, missing


def write_plan(campaign, recipes=None):
    path, _, _ = load_campaign(campaign)
    with lock(path / ".lock"):
        path, frozen, _ = load_campaign(path)
        if (path / "plan-lock.json").exists():
            raise ValueError("Plan already sealed; re-freeze after changing recipes")
        plan, missing = compile_plan(frozen, recipes)
        atomic(path / "tasks.json", plan)
        atomic(path / "planning-gaps.json", {"missing": missing})
    return {"tasks": len(plan["tasks"]), "missing": missing}
