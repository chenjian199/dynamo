# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independent deterministic domain assertions over collected measurements, never AI verdicts."""

import argparse
import json
import math
from pathlib import Path

from adapter.workflow.bindings import case_environment
from adapter.workflow.core import atomic, digest, read


def pointer(data, expression):
    value = data
    for part in expression.strip("/").split("/"):
        if expression == "":
            return data
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def evaluate(check, observation, root, envelope):
    op = check["op"]
    actual = pointer(observation, check["path"])
    expected = (
        pointer(envelope, check["expected_from"])
        if "expected_from" in check
        else check.get("value")
    )
    if op == "equal":
        return type(actual) is type(expected) and actual == expected
    if op == "positive":
        return type(actual) in (int, float) and math.isfinite(actual) and actual > 0
    if op == "minimum":
        return (
            type(actual) in (int, float)
            and math.isfinite(actual)
            and actual >= expected
        )
    if op == "maximum":
        return (
            type(actual) in (int, float)
            and math.isfinite(actual)
            and actual <= expected
        )
    if op == "nonempty":
        return isinstance(actual, (list, dict, str)) and len(actual) > 0
    if op == "equal_fields":
        other = pointer(observation, check["other"])
        return (
            not isinstance(actual, bool)
            and not isinstance(other, bool)
            and actual == other
            and (not check.get("nonempty", False) or bool(actual))
        )
    if op == "different_fields":
        return actual != pointer(observation, check["other"])
    if op == "file_parity":
        other = pointer(observation, check["other"])
        files = [(root / name).resolve() for name in (actual, other)]
        return all(
            p.is_relative_to(root) and p.is_file() and p.stat().st_size > 0
            for p in files
        ) and digest(files[0]) == digest(files[1])
    if op == "unique_minimum":
        return isinstance(actual, list) and len(set(actual)) >= expected
    if op == "bounded_growth":
        return (
            isinstance(actual, list)
            and len(actual) >= 3
            and all(type(x) in (int, float) and math.isfinite(x) for x in actual)
            and max(actual[-3:]) - min(actual[:3]) <= expected
        )
    raise ValueError("Unknown deterministic check: " + op)


def evaluate_case(case, attempt, envelope):
    root = Path(attempt).resolve()
    observation_path = root / "cases" / case["id"] / "observations.json"
    assertions, errors = {}, []
    try:
        observation = read(observation_path)
        env, binding = case_environment(
            envelope["environment"], envelope["feature"], case
        )
        manifests = {
            n["id"]: {m["id"]: m["manifest_sha256"] for m in n.get("models", [])}
            for n in env["nodes"]
        }
        envelope = dict(envelope, case_binding=binding, model_manifests=manifests)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {}, [str(exc)]
    for name, checks in case.get("checks", {}).items():
        try:
            passed = all(evaluate(c, observation, root, envelope) for c in checks)
        except (ValueError, OSError, TypeError, KeyError, IndexError) as exc:
            passed = False
            errors.append(f"{case['id']}/{name}: {exc}")
        assertions[name] = {
            "passed": passed,
            "evidence": [case["id"] + "/observations"],
        }
        if not passed:
            errors.append(f"{case['id']}/{name}: domain assertion failed")
    return assertions, errors


def verify(envelope_path):
    envelope_path = Path(envelope_path).resolve()
    root, envelope = envelope_path.parent, read(envelope_path)
    feature = envelope["feature"]
    artifacts = []
    roles = [
        ("command", "task.json"),
        ("environment", "envelope.json"),
        ("verifier", "verifier-program.json"),
    ]
    roles += [("raw_output", p.name) for p in sorted(root.glob("run-*.log"))]
    for role, name in roles:
        artifacts.append(
            {"id": name, "path": name, "role": role, "sha256": digest(root / name)}
        )
    common = [a["id"] for a in artifacts]
    cases, diagnostics = [], []
    for case in feature["cases"]:
        observation = root / "cases" / case["id"] / "observations.json"
        assertion, errors = evaluate_case(case, root, envelope)
        diagnostics.extend(errors)
        ident = case["id"] + "/observations"
        if observation.is_file():
            artifacts.append(
                {
                    "id": ident,
                    "path": str(observation.relative_to(root)),
                    "role": "measurements",
                    "sha256": digest(observation),
                }
            )
        cases.append(
            {
                "id": case["id"],
                "artifacts": common + ([ident] if observation.is_file() else []),
                "assertions": assertion,
            }
        )
    result = {
        "identity": envelope["identity"],
        "feature_id": feature["id"],
        "feature_revision": feature["revision"],
        "evidence_origin": "current_run",
        "artifacts": artifacts,
        "cases": cases,
    }
    atomic(root / "result.json", result)
    atomic(root / "verifier-diagnostics.json", {"errors": diagnostics})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope", type=Path, required=True)
    args = parser.parse_args()
    verify(args.envelope)
    # Verdict is read from assertions by the gate; exit zero means verifier ran.
    print(json.dumps({"verifier_completed": True}))
