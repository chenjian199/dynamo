# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Explicit exact-input evidence reuse, with revalidation of the original immutable attempt."""

from adapter.workflow.core import (
    ROOT,
    atomic,
    digest,
    event,
    fingerprint,
    load_campaign,
    lock,
    now,
    read,
)
from adapter.workflow.evidence.qualify import qualify
from adapter.workflow.scope import accepted


def equivalence_key(frozen, key):
    feature = frozen["features"][key]
    return fingerprint(
        {
            "platform": frozen["platform"],
            "chip": frozen["chip"],
            "stack": frozen["stack_sha256"],
            "environment": frozen["environment"],
            "feature": feature,
            "source_inputs": frozen.get("feature_inputs", {}).get(
                key, frozen["source"]
            ),
            "dependencies": {
                d: equivalence_key(frozen, d) for d in feature["dependencies"]
            },
        }
    )


def evidence_for(path, frozen, state, key, visited=None):
    visited = set(visited or ())
    marker = (str(path), key)
    if marker in visited:
        raise ValueError("Evidence inheritance cycle")
    visited.add(marker)
    item = state["features"][key]
    if not accepted(item):
        raise ValueError("Feature is not qualified: " + key)
    inherited = item.get("inheritance")
    if inherited:
        source = (ROOT / inherited["campaign"]).resolve()
        if (
            not source.is_relative_to((ROOT / "adapter/results").resolve())
            and source.parent != path.parent
        ):
            raise ValueError("Inherited evidence is outside result storage")
        sp, sf, ss = load_campaign(source)
        if digest(sp / "lock.json") != inherited["lock_sha256"] or equivalence_key(
            sf, key
        ) != equivalence_key(frozen, key):
            raise ValueError("Inherited input identity changed")
        original, gate = evidence_for(sp, sf, ss, key, visited)
        if (
            digest(original / "result.json") != inherited["result_sha256"]
            or digest(original / "execution.json") != inherited["execution_sha256"]
        ):
            raise ValueError("Inherited evidence changed")
        return original, gate
    record = item["attempts"][-1]
    attempt = (path / record["path"]).resolve()
    if not attempt.is_relative_to((path / "features" / key).resolve()):
        raise ValueError("Attempt outside its feature")
    if record.get("execution_sha256") != digest(attempt / "execution.json"):
        raise ValueError("Execution receipt changed or absent")
    gate = qualify(
        read(attempt / "result.json"), frozen, frozen["features"][key], attempt
    )
    if not accepted(gate):
        raise ValueError("; ".join(gate["errors"]))
    return attempt, gate


def inherit(campaign, source_campaign, feature_id, reviewed_by, rationale):
    if not reviewed_by or not rationale:
        raise ValueError("Evidence reuse requires an explicit reviewer and rationale")
    path, _, _ = load_campaign(campaign)
    with lock(path / ".lock"):
        path, frozen, state = load_campaign(path)
        sp, sf, ss = load_campaign(source_campaign)
        key = feature_id
        if equivalence_key(frozen, key) != equivalence_key(sf, key):
            raise ValueError(
                "Evidence reuse requires exact model, topology, stack, cases and affected source inputs"
            )
        item = state["features"][key]
        if item["attempts"] or item.get("inheritance"):
            raise ValueError(
                "Cannot replace target attempt history with inherited evidence"
            )
        attempt, gate = evidence_for(sp, sf, ss, key)
        try:
            source_name = str(sp.relative_to(ROOT))
        except ValueError:  # Isolated CPU test repositories.
            source_name = str(sp)
        item.update(
            implementation="implemented",
            task="succeeded",
            test=gate["test"],
            qualification=gate["qualification"],
            required_passed=gate.get("required_passed", True),
            evidence_origin="inherited",
            inheritance={
                "campaign": source_name,
                "identity": sf["identity"],
                "lock_sha256": digest(sp / "lock.json"),
                "result_sha256": digest(attempt / "result.json"),
                "execution_sha256": digest(attempt / "execution.json"),
                "reviewed_by": reviewed_by,
                "rationale": rationale,
                "time": now(),
            },
        )
        atomic(path / "state.json", state)
        event(
            path,
            "evidence_inherited",
            feature=key,
            source=source_name,
            reviewed_by=reviewed_by,
        )
    return item
