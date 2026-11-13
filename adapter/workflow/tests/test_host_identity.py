# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cloned OS identities must not weaken the distinct physical host gate."""

import copy

import pytest

from adapter.workflow.config import validate_environment
from adapter.workflow.preflight import inspect_environment

UUIDS = ["a6691552-b04f-9b68-ef11-06c062f7d919", "a6891e26-b04f-b1ac-ef11-1cbf7c5530b3"]


def environment():
    return {
        "nodes": [
            {
                "id": str(i),
                "ssh": "node" + str(i),
                "workspace": "/work",
                "host_identity": {"kind": "dmi_product_uuid", "value": value},
            }
            for i, value in enumerate(UUIDS)
        ],
        "budgets": {"task_timeout_seconds": 30, "max_attempts": 1, "max_tasks": 1},
    }


def observe(env, values):
    def runner(argv, host=None, **kwargs):
        if argv == ["cat", "/etc/machine-id"]:
            return {"passed": True, "stdout": "a" * 32}
        if argv == ["cat", "/sys/class/dmi/id/product_uuid"]:
            value = values[int(host[-1])]
            return {"passed": value is not None, "stdout": value or ""}
        return {"passed": True, "stdout": "ok"}

    return inspect_environment(env, ["ssh", "multinode"], runner=runner)


def test_distinct_hardware_with_cloned_os_identity():
    env = environment()
    validate_environment(env)
    result = observe(env, [v.upper() for v in UUIDS])
    assert result["readiness"]["multinode"]
    check = next(c for c in result["checks"] if c["requirement"] == "multinode")
    assert len(set(check["machine_ids"].values())) == 1
    assert len(set(check["hardware_ids"].values())) == 2
    assert check["identity_kind"] == "dmi_product_uuid"


@pytest.mark.parametrize(
    "values", [[UUIDS[0], UUIDS[0]], [UUIDS[0], None], [UUIDS[0], "wrong"]]
)
def test_wrong_or_missing_observation_rejected(values):
    result = observe(environment(), values)
    assert not result["readiness"]["ssh"]
    assert not result["readiness"]["multinode"]


def test_two_aliases_of_same_hardware_still_rejected():
    env = environment()
    env["nodes"][1]["host_identity"] = copy.deepcopy(env["nodes"][0]["host_identity"])
    result = observe(env, [UUIDS[0], UUIDS[0]])
    assert result["readiness"]["ssh"]
    assert not result["readiness"]["multinode"]


def test_partial_binding_cannot_mix_identity_sources():
    env = environment()
    del env["nodes"][1]["host_identity"]
    assert not observe(env, UUIDS)["readiness"]["multinode"]


@pytest.mark.parametrize(
    "binding",
    [
        {"kind": "hostname", "value": "node1"},
        {"kind": "dmi_product_uuid", "value": "00000000-0000-0000-0000-000000000000"},
        {"kind": "dmi_product_uuid", "value": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
        {"kind": "dmi_product_uuid", "value": "bad"},
        "invalid",
    ],
)
def test_identity_declaration_rejects_unusable_values(binding):
    env = environment()
    env["nodes"][0]["host_identity"] = binding
    with pytest.raises(ValueError, match="host_identity"):
        validate_environment(env)
