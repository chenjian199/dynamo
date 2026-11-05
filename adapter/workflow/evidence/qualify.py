# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strict evidence gate, separate from command and AI success."""

from pathlib import Path

import jsonschema

from adapter.workflow.core import ADAPTER, ROOT, digest, fingerprint, read
from adapter.workflow.executors.commands import substitute


def execution_evidence(root, frozen, feature):
    """Bind a verdict to an actual runner attempt, not self-labelled evidence files.

    This is an integrity check inside a trusted workspace, not a signature or a
    sandbox against an author who can rewrite the runner and its audit records.
    Domain correctness still requires a reviewed, independent verifier.
    """
    receipt = read(root / "execution.json")
    task = read(root / "task.json")
    envelope = read(root / "envelope.json")
    if (
        receipt.get("identity") != frozen["identity"]
        or receipt.get("feature_id") != feature["id"]
        or receipt.get("attempt") != root.name
        or envelope.get("identity") != frozen["identity"]
        or envelope.get("feature") != feature
        or envelope.get("environment") != frozen["environment"]
        or receipt.get("task_sha256") != fingerprint(task)
        or receipt.get("completed") is not True
    ):
        raise ValueError("Runner execution identity or completion mismatch")
    campaign = root.parents[2]
    seal = read(campaign / "plan-lock.json")
    if (
        seal.get("identity") != frozen["identity"]
        or seal.get("plan_sha256") != fingerprint(seal.get("plan"))
        or seal.get("plan_sha256") != receipt.get("plan_sha256")
        or task not in seal.get("plan", {}).get("tasks", [])
    ):
        raise ValueError("Runner task is not bound to the frozen plan")
    records = receipt.get("commands", [])
    expected = [
        (phase, i)
        for phase in ("prepare", "run", "observe", "cleanup", "verify")
        for i in range(len(task.get(phase, [])))
    ]
    if [(c.get("phase"), c.get("index")) for c in records] != expected:
        raise ValueError("Runner command coverage mismatch")
    if any(
        c.get("returncode") != 0
        and not (
            task[phase][index].get("optional")
            and phase in {"prepare", "run", "observe"}
        )
        for c, (phase, index) in zip(records, expected)
    ):
        raise ValueError("Runner command or cleanup did not succeed")
    for record, (phase, index) in zip(records, expected):
        spec = substitute(
            task[phase][index], frozen["environment"], ROOT, root, frozen["identity"]
        )
        if record.get("command") != spec:
            raise ValueError("Runner command differs from sealed task")
    required = {"task.json", "envelope.json", "preflight.json", "result.json"}
    required.update(f"{phase}-{i:02}.log" for phase, i in expected)
    files = receipt.get("files", {})
    if not required.issubset(files):
        raise ValueError("Runner evidence snapshot incomplete")
    for name, checksum in files.items():
        file = (root / name).resolve()
        if not file.is_relative_to(root) or digest(file) != checksum:
            raise ValueError("Runner evidence changed: " + name)
    inventory = read(root / "preflight.json")
    if inventory.get("identity") != frozen["identity"] or inventory.get(
        "environment_sha256"
    ) != fingerprint(frozen["environment"]):
        raise ValueError("Runner preflight environment mismatch")
    return files


def qualify(result, frozen, feature, attempt_root):
    errors = []
    warnings = []
    try:
        jsonschema.validate(
            result, read(ADAPTER / "contracts/schemas/result.schema.json")
        )
    except jsonschema.ValidationError as exc:
        return {
            "qualification": "pending",
            "test": "inconclusive",
            "errors": ["Invalid result schema: " + exc.message],
        }

    if result.get("identity") != frozen["identity"]:
        errors.append("identity mismatch")
    if result.get("feature_id") != feature["id"]:
        errors.append("feature mismatch")
    if result.get("feature_revision") != feature["revision"]:
        errors.append("feature revision mismatch")
    if result.get("evidence_origin") != "current_run":
        errors.append(
            "historical/inherited evidence requires an explicit equivalence review"
        )
    artifacts = result.get("artifacts", [])
    found = {}
    root = Path(attempt_root).resolve()
    try:
        executed_files = execution_evidence(root, frozen, feature)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        errors.append("Unbound execution evidence: " + str(exc))
        executed_files = {}
    for artifact in artifacts:
        path = (root / artifact["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            errors.append("artifact absent or outside attempt directory")
            continue
        if digest(path) != artifact.get("sha256"):
            errors.append("artifact checksum mismatch")
            continue
        if artifact.get("id") in found:
            errors.append("duplicate artifact ID")
        found[artifact.get("id")] = artifact
        role, name = artifact.get("role"), artifact["path"]
        if role in {"command", "environment", "raw_output", "verifier"}:
            valid = {
                "command": name == "task.json",
                "environment": name in {"envelope.json", "preflight.json"},
                "raw_output": name.startswith("run-") and name.endswith(".log"),
                "verifier": name.startswith("verify-") and name.endswith(".log"),
            }[role]
            if role == "verifier" and name == "verifier-program.json":
                valid = True
            if not valid or name not in executed_files:
                errors.append("Evidence role not bound to runner output: " + str(role))
    observed = {case.get("id"): case for case in result.get("cases", [])}
    if len(observed) != len(result.get("cases", [])):
        errors.append("duplicate case ID")
    if set(observed) - {case["id"] for case in feature["cases"]}:
        errors.append("case set differs from frozen feature contract")
    for contract in feature["cases"]:
        start_errors = len(errors)
        if contract.get("checks"):
            from adapter.workflow.evidence.verify import evaluate_case

            _, failures = evaluate_case(contract, root, read(root / "envelope.json"))
            errors.extend(failures)
        case = observed.get(contract["id"], {})
        assertions = case.get("assertions", {})
        for name in contract["required_assertions"]:
            a = assertions.get(name, {})
            if (
                a.get("passed") is not True
                or not a.get("evidence")
                or not all(x in found for x in a["evidence"])
            ):
                errors.append(
                    f"{contract['id']}/{name}: missing passing assertion and hashed evidence"
                )
        roles = {found[x].get("role") for x in case.get("artifacts", []) if x in found}
        if any(x not in found for x in case.get("artifacts", [])):
            errors.append(f"{contract['id']}: unknown case artifact")
        for role in contract["evidence_requirements"]:
            if role not in roles:
                errors.append(f"{contract['id']}: missing evidence role {role}")
        if (
            feature.get("case_scope", {}).get(contract["id"], {}).get("disposition")
            == "optional"
        ):
            warnings.extend(errors[start_errors:])
            del errors[start_errors:]
    return {
        "qualification": ("limited" if warnings else "verified")
        if not errors
        else "pending",
        "test": "passed" if not errors else "inconclusive",
        "errors": errors,
        "warnings": warnings,
        "required_passed": not errors,
    }
