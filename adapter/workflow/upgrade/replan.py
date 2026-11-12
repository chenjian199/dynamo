# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fork an immutable upgrade scope without reinterpreting historical test results."""

from copy import deepcopy
from pathlib import Path

from adapter.workflow.core import (
    atomic,
    digest,
    event,
    fingerprint,
    identifier,
    lock,
    now,
    read,
)
from adapter.workflow.upgrade import session
from adapter.workflow.upgrade.analysis import analyze


def replan_scope(source, plan_file, name, reviewed_by, reason):
    if not reviewed_by.strip() or not reason.strip():
        raise ValueError("Scope replan requires reviewer and acceptance rationale")
    destination = session.RESULTS / identifier(name)
    if destination.exists():
        raise ValueError("Session exists; never overwrite a frozen scope")
    with lock(Path(source) / ".lock"):
        path, frozen, state, _ = session.load(source)
        _, head = session.candidate(frozen, state)
        plan = session.validate_plan(read(plan_file))
        mutable = {"id", "deferred_features"}
        if {k: v for k, v in plan.items() if k not in mutable} != {
            k: v for k, v in frozen["plan"].items() if k not in mutable
        }:
            raise ValueError(
                "Scope replan cannot change refs, images, mandatory features or checks"
            )
        if plan.get("deferred_features", {}) == frozen["plan"].get(
            "deferred_features", {}
        ):
            raise ValueError("Scope did not change")
        parent_lock, parent_state = read(path / "lock.json"), deepcopy(state)
        new = deepcopy(frozen)
        new.pop("identity")
        new.update(plan=plan, created=now())
        new["replanned_from"] = {
            "path": str(path),
            "lock_sha256": digest(path / "lock.json"),
            "state_sha256": digest(path / "state.json"),
            "candidate": head,
            "reviewed_by": reviewed_by,
            "reason": reason,
        }
        new["identity"] = fingerprint(new)
        analysis = analyze(frozen["repository"], new)
        # mkdir is exclusive even if two callers chose the same destination name.
        destination.mkdir(parents=True)
        atomic(destination / "parent-lock.json", parent_lock)
        atomic(destination / "parent-state.json", parent_state)
        atomic(destination / "lock.json", new)
        atomic(destination / "analysis.json", analysis)
        # Only same-candidate source reviews survive a scope-only change. Runtime
        # campaign, comparison, check and delivery gates must be attached again.
        reviews = {k: v for k, v in state["reviews"].items() if v.get("commit") == head}
        upstream = state.get("upstream_review")
        atomic(
            destination / "state.json",
            {
                "identity": new["identity"],
                "images": state["images"],
                "candidate": state["candidate"],
                "reviews": reviews,
                "upstream_review": upstream
                if upstream and upstream.get("commit") == head
                else None,
                "checks": [],
                "campaigns": {},
                "comparisons": {},
                "deliveries": {},
            },
        )
        event(
            destination,
            "scope_replanned",
            parent=str(path),
            candidate=head,
            reviewed_by=reviewed_by,
            reason=reason,
            reused_source_reviews=len(reviews),
        )
    return destination
