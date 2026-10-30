# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prevent a healthy deployment with an inactive Planner from passing the audit."""
import json
from datetime import datetime, timezone

import pytest

from check_online_planning import audit

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0]


@pytest.mark.parametrize(
    "reason,demand,when,identity,expected",
    [
        ("scale", 1, 5, "abc", True),
        ("model_not_ready", 1, 5, "abc", False),
        ("scale", 0, 5, "abc", False),
        ("scale", 1, 30, "abc", False),
        ("scale", 1, 5, "def", False),
    ],
)
def test_requires_active_planning_during_load(
    tmp_path, reason, demand, when, identity, expected
):
    start = datetime(2026, 10, 30, tzinfo=timezone.utc).timestamp()
    load = tmp_path / "load.log"
    load.write_text(
        "REQUEST="
        + json.dumps({"stage": "load", "completed_at": start + 10, "e2e_ms": 10000})
        + "\n"
    )
    planner = tmp_path / "planner.log"
    prefix = f"2026-10-30T00:00:{when:02}.000000Z "
    planner.write_text(
        "".join(
            f"Offline bootstrap source=files component={role} samples=6 fingerprint={identity}\n"
            for role in ("prefill", "decode")
        )
        + prefix
        + f"Prefill: {demand:.2f} rps / 10.00 = 1\n"
        + prefix
        + f"Decode: {demand:.2f} rps / 5.00 = 1\n"
        + prefix
        + f"[summary] HOLD throughput_reason={reason}\n"
    )
    assert audit(planner, load, "abc")["passed"] is expected
