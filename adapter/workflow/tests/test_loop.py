# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline harness substitution tests; no model account or hardware calls."""

from unittest.mock import patch

import pytest

from adapter.workflow.core import atomic, load_campaign, read
from adapter.workflow.state.loop import automate, diagnose


@pytest.fixture
def fixture():
    from test_workflow import WorkflowTest

    helper = WorkflowTest()
    helper.setUp()
    helper.init()
    try:
        yield helper
    finally:
        helper.doCleanups()


def test_automatic_refreeze_and_qualification(fixture):
    path = fixture.campaign
    proposal = path / "proposal.json"
    atomic(
        proposal,
        {
            "feature_id": "M01-F01",
            "status": "implemented",
            "tasks_json": "{}",
            "blockers": [],
        },
    )
    calls = []

    def agent(campaign, key, harness, model, invoke):
        calls.append((key, harness, model, invoke))
        return {"result": str(proposal)}

    final = {"features": {"M01-F01": {"qualification": "verified"}}}
    with (
        patch("adapter.workflow.state.loop.write_plan", return_value={"missing": []}),
        patch("adapter.workflow.state.loop.doctor", return_value={"passed": True}),
        patch("adapter.workflow.state.loop.run", return_value=final) as run,
    ):
        result = automate(path, "claude", "explicit-fixture-model", 1, 120, agent)
    assert result["status"] == "qualified_declared_scope" and result["agent_calls"] == 1
    assert calls == [("M01-F01", "claude", "explicit-fixture-model", True)]
    target = path.with_name(path.name + "-cycle-1")
    assert (target / "lock.json").exists() and (target / "parent.json").exists()
    assert run.call_args.args == (target, "M01-F01")
    assert read(path / "loop.json")["limits"] == {"max_cycles": 1, "wall_seconds": 120}


def test_interrupted_loop_does_not_replay_model(fixture):
    def crash(*args):
        raise RuntimeError("simulated disconnect")

    with pytest.raises(RuntimeError):
        automate(fixture.campaign, "codex", "model", 1, 120, crash)
    with pytest.raises(ValueError, match="Interrupted"):
        automate(fixture.campaign, "openai", "model", 1, 120, crash)
    assert read(fixture.campaign / "loop.json")["agent_calls"] == 1


def test_blocked_agent_preserves_proposal_and_budget(fixture):
    proposal = fixture.campaign / "proposal.json"
    atomic(proposal, {"status": "blocked", "blockers": ["missing asset"]})
    result = automate(
        fixture.campaign,
        "openai",
        "model",
        1,
        120,
        lambda *a: {"result": str(proposal)},
    )
    assert result["status"] == "agent_blocked" and result["cycles"][0]["blockers"] == [
        "missing asset"
    ]
    with pytest.raises(ValueError, match="immutable"):
        automate(fixture.campaign, "openai", "model", 2, 120)


def test_native_failure_requires_actual_paired_evidence(fixture):
    path, _, state = load_campaign(fixture.campaign)
    item = state["features"]["M01-F01"]
    item["attempts"] = [{"path": "features/M01-F01/attempt-001"}]
    observation = path / item["attempts"][0]["path"] / "cases/C/observations.json"
    atomic(observation, {"failure_classification": "native_engine"})
    assert diagnose(path, state, "M01-F01")["category"] == "inconclusive"
