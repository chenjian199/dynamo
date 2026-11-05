# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Make incomplete agreed scope visible without turning structural checks into certification."""

from adapter.workflow.core import ADAPTER, ROOT, read


def workflow_audit():
    audit = read(ADAPTER / "contracts/workflow-audit.yaml")
    criteria = audit["criteria"]
    if len({c["id"] for c in criteria}) != len(criteria):
        raise ValueError("Duplicate workflow audit criterion")
    for criterion in criteria:
        if criterion["status"] not in {"implemented", "partial", "unverified"}:
            raise ValueError("Unknown workflow audit status")
        if type(criterion["required"]) is not bool:
            raise ValueError("Workflow audit required must be boolean")
        if criterion["status"] == "implemented" and criterion["remaining"]:
            raise ValueError("Implemented criterion still has remaining work")
        if not criterion["evidence"] or any(
            not (ROOT / p).exists() for p in criterion["evidence"]
        ):
            raise ValueError("Workflow criterion has absent evidence references")
    blockers = [
        c["id"] for c in criteria if c["required"] and c["status"] != "implemented"
    ]
    if audit["status"] not in {"ready", "not_ready"}:
        raise ValueError("Unknown finalization status")
    if audit["status"] == "ready" and blockers:
        raise ValueError(
            "Cannot declare ready while required criteria remain incomplete"
        )
    return {
        "proposed_version": audit["proposed_version"],
        "status": audit["status"],
        "ready": not blockers and audit["status"] == "ready",
        "blockers": blockers,
        "note": "Reviewed scope tracking, not an automatic proof of implementation or hardware certification.",
    }
