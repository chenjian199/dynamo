# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Delivery orchestration fixtures. Real archive round trips are recorded separately."""

from unittest.mock import patch

import pytest

from adapter.workflow.core import atomic
from adapter.workflow.delivery import PHASES, deliver


@pytest.fixture
def delivery(tmp_path):
    frozen = {
        "identity": "fixture",
        "stack_sha256": "fixture-stack",
        "stack": {"versions": {"vllm": "fixture"}},
        "scope": {},
        "environment": {
            "execution_enabled": True,
            "budgets": {"task_timeout_seconds": 5},
        },
    }
    plan = {
        "directory": "delivery",
        "smoke_report": "smoke.json",
        "archive": "bundle.tar",
        "phases": {},
        "outputs": {},
    }
    for phase, entries in PHASES.items():
        plan["phases"][phase] = [
            {
                "argv": [
                    "python3",
                    "{repo}/" + (entries[0] if entries else "fixture-smoke.py"),
                ]
            }
        ]
        plan["outputs"][phase] = [phase + ".json"]
    atomic(tmp_path / "plan.json", plan)
    atomic(tmp_path / "lock.json", frozen)
    with (
        patch(
            "adapter.workflow.delivery.load_campaign",
            return_value=(tmp_path, frozen, {}),
        ),
        patch("adapter.workflow.delivery.check_freshness"),
    ):
        yield tmp_path, plan, frozen


def fixture_executor(root, wrong=False, crash=None):
    def execute(spec, env, cwd, log, timeout, on_start):
        phase = log.name.split("-")[0]
        log.write_text("fixture command\n")
        if crash == phase:
            raise RuntimeError("fixture process interruption")
        atomic(root / (phase + ".json"), {"phase": phase, "fixture": True})
        if phase == "smoke":
            atomic(
                root / "smoke.json",
                {
                    "identity": "fixture",
                    "stack_sha256": "fixture-stack",
                    "versions": {"vllm": "wrong" if wrong else "fixture"},
                    "requests": [
                        {
                            "http_status": 200,
                            "output_tokens": 1,
                            "actual": [1],
                            "expected": [1],
                        }
                    ],
                    "images": [
                        {"role": r, "expected_id": "id", "observed_id": "id"}
                        for r in ("runtime", "dev", "tools", "operator")
                    ],
                },
            )
        if phase == "seal":
            (root / "bundle.tar").write_bytes(b"fixture archive only")
        return 0

    return execute


def test_delivery_sequence_resume_and_tamper(delivery):
    path, _, _ = delivery
    root = path / "delivery"
    with patch(
        "adapter.workflow.delivery.execute", side_effect=fixture_executor(root)
    ) as call:
        result = deliver(path, path / "plan.json")
        assert result["status"] == "artifacts_sealed" and call.call_count == 7
        deliver(path, path / "plan.json")
        assert call.call_count == 7
        (root / "export.json").write_text("tampered")
        with pytest.raises(ValueError, match="changed"):
            deliver(path, path / "plan.json")
        assert call.call_count == 7


def test_import_identity_failure_blocks_seal(delivery):
    path, _, _ = delivery
    with patch(
        "adapter.workflow.delivery.execute",
        side_effect=fixture_executor(path / "delivery", wrong=True),
    ) as call:
        with pytest.raises(ValueError, match="version"):
            deliver(path, path / "plan.json")
        assert call.call_count == 6 and not (path / "delivery/bundle.tar").exists()


def test_delivery_interruption_never_blindly_reimports(delivery):
    path, _, _ = delivery
    with patch(
        "adapter.workflow.delivery.execute",
        side_effect=fixture_executor(path / "delivery", crash="import"),
    ) as call:
        with pytest.raises(RuntimeError):
            deliver(path, path / "plan.json")
        count = call.call_count
        with pytest.raises(ValueError, match="Interrupted"):
            deliver(path, path / "plan.json")
        assert call.call_count == count


def test_missing_delivery_phase_rejected(delivery):
    path, plan, _ = delivery
    del plan["phases"]["verify_archive"]
    atomic(path / "plan.json", plan)
    with pytest.raises(ValueError, match="requires"):
        deliver(path, path / "plan.json")
