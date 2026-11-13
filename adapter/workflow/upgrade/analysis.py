# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Conservative two-delta impact analysis; textual merge success is not qualification."""

import hashlib

from adapter.workflow.identity import COMMON, relevant
from adapter.workflow.upgrade.gitops import changes


def dependencies(feature):
    return feature["dependencies"] + [
        k for group in feature.get("dependencies_any_of", []) for k in group
    ]


def closure(features, seeds):
    result = set(seeds)
    while True:
        # Invalidate dependants, then include prerequisites needed to execute their contracts.
        extra = {k for k, f in features.items() if set(dependencies(f)) & result}
        extra |= {d for k in result for d in dependencies(features[k])}
        if extra <= result:
            return sorted(result)
        result |= extra


def analyze(repo, frozen):
    refs, features = frozen["commits"], frozen["features"]
    local = changes(repo, refs["base"], refs["heyi"])
    upstream = changes(repo, refs["base"], refs["target"])
    upstream_paths = {x["path"] for x in upstream}
    mapping, unknown = {}, []
    for entry in local + upstream:
        path = entry["path"]
        if path.startswith("adapter/results/"):
            continue
        matches = [
            k
            for k, f in features.items()
            if relevant(
                path,
                list(COMMON)
                + f["implementation_paths"]
                + f.get("entrypoints", [])
                + [f["contract_path"]],
            )
        ]
        mapping[path] = sorted(matches)
        if not matches:
            unknown.append(path)
    # Unclassified changes cannot silently escape testing, including new upstream interfaces.
    affected = set(frozen["plan"]["mandatory_features"])
    for p in upstream_paths:
        affected.update(mapping.get(p, []))
    if unknown:
        affected.update(features)
    # Each requested engine change invalidates the old platform results independently of Git overlap.
    if any(
        t["vllm"] != frozen["stacks"][p]["versions"]["vllm"]
        for p, t in frozen["plan"]["platforms"].items()
    ):
        affected.update(features)
    ledger = []
    historical = []
    for entry in local:
        path = entry["path"]
        if path.startswith("adapter/results/"):
            historical.append(entry)
            continue
        ledger.append(
            dict(
                entry,
                id="AD-" + hashlib.sha256(path.encode()).hexdigest()[:16],
                features=mapping[path],
                platforms=list(frozen["plan"]["platforms"]),
                upstream_overlap=path in upstream_paths,
                disposition="pending",
                note="Review intent and target APIs, even when merge is clean",
            )
        )
    required = closure(features, affected)
    deferred = frozen["plan"].get("deferred_features", {})
    if set(deferred) - set(features):
        raise ValueError("Unknown deferred upgrade feature")
    if set(deferred) & set(frozen["plan"]["mandatory_features"]):
        raise ValueError("Mandatory upgrade features cannot be deferred")
    retained = set(required) - set(deferred)
    for key in retained:
        feature = features[key]
        if set(feature["dependencies"]) & set(deferred):
            raise ValueError(key + ": required feature has a deferred dependency")
        for group in feature.get("dependencies_any_of", []):
            if set(group) <= set(deferred):
                raise ValueError(key + ": all alternative dependencies are deferred")
    result = {
        "schema_version": 1,
        "commits": refs,
        "upstream": upstream,
        "ledger": ledger,
        "historical_paths": historical,
        "unclassified_paths": sorted(set(unknown)),
        "required_features": sorted(retained),
        "new_upstream_review": "pending",
        "evidence_policy": "Old results remain historical; all selected features need fresh campaigns.",
    }
    # Preserve the exact analysis shape for existing immutable, full-scope sessions.
    if deferred:
        result["deferred_features"] = deferred
    return result
