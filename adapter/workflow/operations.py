# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CLI operations for recipes, transfers, owned resources, AI loops and evidence reuse."""

from pathlib import Path

from adapter.workflow.core import read

NAMES = [
    "compile",
    "stage",
    "stage-inputs",
    "collect",
    "launch-job",
    "launch-process",
    "observe-resources",
    "recover",
    "inherit",
    "automate",
    "deliver",
]


def parsers(sub):
    for name in NAMES:
        p = sub.add_parser(name)
        p.add_argument("--campaign", type=Path, required=True)
        if name == "compile":
            p.add_argument("--recipes", type=Path)
        if name in {"stage", "stage-inputs", "collect", "launch-process"}:
            p.add_argument("--node", required=True)
        if name in {
            "collect",
            "stage-inputs",
            "launch-job",
            "launch-process",
            "observe-resources",
            "recover",
            "inherit",
        }:
            p.add_argument("--feature", required=True)
        if name in {
            "collect",
            "stage-inputs",
            "launch-job",
            "launch-process",
            "observe-resources",
        }:
            p.add_argument("--attempt", required=True)
        if name == "collect":
            p.add_argument("--promote-cases", action="store_true")
        if name == "launch-job":
            p.add_argument("--manifest", type=Path, required=True)
        if name == "launch-process":
            p.add_argument("--name", required=True)
            p.add_argument("--argv", nargs="+", required=True)
        if name in {"observe-resources", "recover"}:
            p.add_argument("--stop-owned", action="store_true")
        if name == "inherit":
            p.add_argument("--source", type=Path, required=True)
            p.add_argument("--reviewed-by", required=True)
            p.add_argument("--rationale", required=True)
        if name == "automate":
            p.add_argument(
                "--harness", choices=["codex", "claude", "openai"], required=True
            )
            p.add_argument("--model", required=True)
            p.add_argument("--max-cycles", type=int, default=3)
            p.add_argument("--wall-seconds", type=int, default=1800)
        if name == "deliver":
            p.add_argument("--plan", type=Path, required=True)


def dispatch(a):
    from adapter.workflow.delivery import deliver
    from adapter.workflow.evidence.inheritance import inherit
    from adapter.workflow.executors.resources import (
        launch_job,
        launch_process,
        observe,
        recover,
    )
    from adapter.workflow.executors.transfers import collect, stage, stage_inputs
    from adapter.workflow.state.compile import write_plan
    from adapter.workflow.state.loop import automate

    if a.command == "compile":
        return write_plan(a.campaign, read(a.recipes) if a.recipes else None)
    if a.command == "stage-inputs":
        return stage_inputs(a.campaign, a.node, a.feature, a.attempt)
    if a.command == "stage":
        return stage(a.campaign, a.node)
    if a.command == "collect":
        return collect(a.campaign, a.node, a.feature, a.attempt, a.promote_cases)
    if a.command == "launch-job":
        return launch_job(a.campaign, a.feature, a.attempt, a.manifest)
    if a.command == "launch-process":
        return launch_process(a.campaign, a.feature, a.attempt, a.node, a.name, a.argv)
    if a.command == "observe-resources":
        return observe(a.campaign, a.feature, a.attempt, a.stop_owned)
    if a.command == "recover":
        return recover(a.campaign, a.feature, a.stop_owned)
    if a.command == "inherit":
        return inherit(a.campaign, a.source, a.feature, a.reviewed_by, a.rationale)
    if a.command == "automate":
        return automate(a.campaign, a.harness, a.model, a.max_cycles, a.wall_seconds)
    return deliver(a.campaign, a.plan)
