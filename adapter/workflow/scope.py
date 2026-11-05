# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Immutable scope and dependency selection; exclusions never become passing tests."""

from copy import deepcopy

DISPOSITIONS = {"required", "optional", "deferred", "out_of_scope", "not_applicable"}


def validate_disposition(value):
    if not isinstance(value, dict) or value.get("disposition") not in DISPOSITIONS:
        raise ValueError("Scope needs an explicit disposition")
    if value["disposition"] in {"deferred", "not_applicable"} and not all(
        value.get(k) for k in ("reason", "accepted_by", "reference")
    ):
        raise ValueError(
            "Deferred/not-applicable scope needs reason, accepted_by and reference"
        )
    if value["disposition"] == "out_of_scope" and not value.get("reason"):
        raise ValueError("Out-of-scope needs a reason")


def resolve(features, selected, declaration=None):
    declaration = deepcopy(declaration or {})
    declared = declaration.get("features", {})
    if set(declared) - set(features):
        raise ValueError("Unknown feature in scope")
    if selected is None and not declared:
        selected = list(features)
    scope = {
        k: {
            "disposition": "required" if k in (selected or []) else "out_of_scope",
            "reason": "Explicit campaign selection",
        }
        for k in features
    }
    for key, value in declared.items():
        validate_disposition(value)
        scope[key] = value
    choices = declaration.get("dependency_choices", {})
    if set(choices) - set(features):
        raise ValueError("Unknown dependency-choice feature")
    active, done, ordered, contracts = set(), set(), [], {}

    def visit(key):
        if key not in features:
            raise ValueError("Unknown feature: " + key)
        if key in active:
            raise ValueError("Dependency cycle: " + key)
        if key in done:
            return
        active.add(key)
        feature = deepcopy(features[key])
        dependencies = list(feature["dependencies"])
        alternatives = feature.get("dependencies_any_of", [])
        chosen = choices.get(key, [])
        if len(chosen) != len(alternatives):
            raise ValueError(f"{key}: choose one dependency from each any-of group")
        for pick, options in zip(chosen, alternatives):
            if pick not in options:
                raise ValueError(f"{key}: invalid any-of dependency {pick}")
            dependencies.append(pick)
        feature["dependencies"] = list(dict.fromkeys(dependencies))
        for dep in feature["dependencies"]:
            if dep not in scope:
                raise ValueError("Unknown feature: " + dep)
            if dep in declared and scope[dep]["disposition"] in {
                "deferred",
                "out_of_scope",
                "not_applicable",
            }:
                raise ValueError(f"{key}: excluded dependency {dep}")
            needed = (
                "required" if scope[key]["disposition"] == "required" else "optional"
            )
            if scope[dep]["disposition"] != "required":
                scope[dep] = dict(
                    scope[dep], disposition=needed, reason="Dependency of " + key
                )
            visit(dep)
        cases = scope[key].get("cases", {})
        if set(cases) - {c["id"] for c in feature["cases"]}:
            raise ValueError("Unknown case in scope: " + key)
        case_scope = {}
        for case in feature["cases"]:
            value = cases.get(case["id"], {"disposition": "required"})
            validate_disposition(value)
            case_scope[case["id"]] = value
        # Optional cases are still executed, but their failure is separately reported.
        feature["case_scope"] = case_scope
        feature["cases"] = [
            c
            for c in feature["cases"]
            if case_scope[c["id"]]["disposition"] in {"required", "optional"}
        ]
        if not any(
            case_scope[c["id"]]["disposition"] == "required" for c in feature["cases"]
        ):
            raise ValueError("Executable feature must have a required case: " + key)
        contracts[key] = feature
        active.remove(key)
        done.add(key)
        ordered.append(key)

    for key in scope:
        if scope[key]["disposition"] in {"required", "optional"}:
            visit(key)
    # Shared dependencies first visited through an optional branch may later become required.
    while True:
        promoted = False
        for key, contract in contracts.items():
            if scope[key]["disposition"] == "required":
                for dependency in contract["dependencies"]:
                    if scope[dependency]["disposition"] != "required":
                        scope[dependency]["disposition"] = "required"
                        promoted = True
        if not promoted:
            break
    return {"features": scope, "dependency_choices": choices}, ordered, contracts


def accepted(item):
    return item.get("qualification") == "verified" or (
        item.get("qualification") == "limited" and item.get("required_passed") is True
    )
