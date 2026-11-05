# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded execution, persistent attempts, leases, and explicit interruption recovery."""

import contextlib
import datetime
import os
import subprocess
import time
from pathlib import Path

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
    source_identity,
)
from adapter.workflow.evidence.inheritance import evidence_for
from adapter.workflow.evidence.qualify import qualify
from adapter.workflow.executors.commands import execute, substitute
from adapter.workflow.identity import freshness_map
from adapter.workflow.scope import accepted
from adapter.workflow.state.plan import sealed_plan, validate_plan


def check_freshness(frozen, selected=None):
    status = freshness_map(frozen, source_identity(), fingerprint)
    if any(status[k]["freshness"] != "valid" for k in (selected or status)):
        raise ValueError(
            "Source changed since freeze: evidence is stale; initialize a new campaign"
        )
    if digest(ROOT / frozen["stack_profile"]) != frozen["stack_sha256"]:
        raise ValueError("Stack changed since freeze; initialize a new campaign")
    for key in selected or frozen["features"]:
        feature = frozen["features"][key]
        if digest(ROOT / feature["contract_path"]) != feature["contract_sha256"]:
            raise ValueError("Feature contract changed; initialize a new campaign")


def process_identity(pid):
    """Linux boot/start ticks disambiguate PID reuse; never kill a recorded PID."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "start_ticks": stat[19],
        }
    except FileNotFoundError:
        return None


def audit_completed(path, frozen, state):
    """Resume and release both recheck every certified dependency, never trust a flag."""
    failures = {}
    for key, item in state["features"].items():
        if not accepted(item):
            continue
        try:
            evidence_for(path, frozen, state, key)
            for dependency in frozen["features"][key]["dependencies"]:
                if not accepted(state["features"][dependency]):
                    raise ValueError("Unverified dependency: " + dependency)
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            failures[key] = str(exc)
    if failures:
        raise ValueError("Evidence changed or dependency unverified: " + str(failures))


def run(campaign, only=None):
    path, frozen, _ = load_campaign(campaign)
    if only and only not in frozen["features"]:
        raise ValueError("Requested feature is outside the frozen campaign")
    if not frozen["environment"].get("execution_enabled"):
        raise ValueError(
            "This environment is plan-only; set execution_enabled before initializing an execution campaign"
        )
    # Always acquire campaign lock before reading mutable plans, preflight or state.
    with lock(path / ".lock"), contextlib.ExitStack() as lease_stack:
        path, frozen, state = load_campaign(path)
        check_freshness(frozen, [only] if only else None)
        preflight = read(path / "preflight.json")
        if preflight.get("identity") != frozen["identity"] or preflight.get(
            "environment_sha256"
        ) != fingerprint(frozen["environment"]):
            raise ValueError("Run doctor for this exact campaign and environment first")
        age = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.datetime.fromisoformat(preflight["time"])
        if not 0 <= age.total_seconds() <= 3600:
            raise ValueError(
                "Preflight expired or timestamp is in the future; rerun doctor"
            )
        tasks = validate_plan(read(path / "tasks.json"), frozen)
        seal = sealed_plan(path, frozen, create=True) if tasks else None
        budgets = frozen["environment"]["budgets"]
        resource_keys = sorted(
            {
                fingerprint(
                    preflight.get("machine_ids", {}).get(n["id"])
                    or n.get("machine_id")
                    or n.get("ssh")
                    or "localhost"
                )
                for n in frozen["environment"]["nodes"]
            }
        )
        leases = Path(
            os.environ.get(
                "ADAPTER_LEASE_DIR", str(Path.home() / ".cache/dynamo-adapter/leases")
            )
        )
        for resource_key in resource_keys:
            lease_stack.enter_context(lock(leases / resource_key))
        audit_completed(path, frozen, state)
        for key in frozen["planned"]:
            if only and key != only:
                continue
            item, feature = state["features"][key], frozen["features"][key]
            if item["task"] == "running" or item.get("reconciliation_required"):
                raise ValueError(
                    f"{key}: interrupted running attempt or uncertain cleanup; reconcile before retrying"
                )
            if accepted(item):
                continue
            task, blocked = tasks.get(key), []
            if not task:
                blocked.append("Task commands not yet planned")
            blocked += (
                [
                    f"preflight:{c['id']}"
                    for c in feature["cases"]
                    if feature.get("case_scope", {}).get(c["id"], {}).get("disposition")
                    != "optional"
                    and not preflight["cases"].get(c["id"], {}).get("passed")
                ]
                if "cases" in preflight
                else [
                    f"preflight:{r}"
                    for r in feature["requirements"]
                    if not preflight["readiness"].get(r)
                ]
            )
            blocked += [
                f"dependency:{d}"
                for d in feature["dependencies"]
                if not accepted(state["features"][d])
            ]
            if blocked:
                item.update(task="blocked", qualification="blocked", reason=blocked)
                atomic(path / "state.json", state)
                event(path, "blocked", feature=key, reasons=blocked)
                continue
            limit = min(feature["retry"]["max_attempts"], budgets["max_attempts"])
            if len(item["attempts"]) >= limit:
                raise ValueError(f"{key}: attempt budget exhausted")
            attempt = (
                path / "features" / key / f"attempt-{len(item['attempts']) + 1:03}"
            )
            attempt.mkdir(parents=True, exist_ok=False)
            record = {
                "path": str(attempt.relative_to(path)),
                "started": now(),
                "task_sha256": fingerprint(task),
                "phase": "prepare",
                "resources": task.get("resources", []),
            }
            item["attempts"].append(record)
            item.update(
                task="running",
                implementation="in_progress",
                test="not_run",
                qualification="pending",
            )
            atomic(attempt / "task.json", task)
            atomic(
                attempt / "verifier-program.json",
                {
                    "commands": task["verify"],
                    "program": "adapter/workflow/evidence/verify.py",
                    "sha256": digest(ROOT / "adapter/workflow/evidence/verify.py"),
                },
            )
            atomic(attempt / "preflight.json", preflight)
            atomic(
                attempt / "resources.json",
                {
                    "identity": frozen["identity"],
                    "attempt": attempt.name,
                    "resources": record["resources"],
                },
            )
            atomic(path / "state.json", state)
            atomic(
                attempt / "envelope.json",
                {
                    "identity": frozen["identity"],
                    "feature": feature,
                    "attempt_directory": str(attempt),
                    "source": {
                        k: v for k, v in frozen["source"].items() if k != "files"
                    },
                    "stack": frozen["stack"],
                    "environment": frozen["environment"],
                    "result_path": str(attempt / "result.json"),
                },
            )
            receipt = {
                "identity": frozen["identity"],
                "feature_id": key,
                "attempt": attempt.name,
                "task_sha256": fingerprint(task),
                "plan_sha256": seal["plan_sha256"],
                "completed": False,
                "commands": [],
                "files": {},
            }
            timeout = min(
                task.get("timeout_seconds", budgets["task_timeout_seconds"]),
                budgets["task_timeout_seconds"],
            )
            deadline = time.monotonic() + timeout

            def phase_run(
                phase,
                phase_deadline,
                record=record,
                task=task,
                attempt=attempt,
                key=key,
                receipt=receipt,
            ):
                record["phase"] = phase
                atomic(path / "state.json", state)
                for i, original in enumerate(task.get(phase, [])):
                    spec = substitute(
                        original,
                        frozen["environment"],
                        ROOT,
                        attempt,
                        frozen["identity"],
                    )
                    step = {
                        "phase": phase,
                        "index": i,
                        "command": spec,
                        "started": now(),
                        "returncode": None,
                    }
                    receipt["commands"].append(step)
                    atomic(attempt / "execution.json", receipt)

                    def started(pid):
                        record["pid"], record["process_identity"] = (
                            pid,
                            process_identity(pid),
                        )
                        atomic(path / "state.json", state)
                        event(
                            path, "process_started", feature=key, phase=phase, pid=pid
                        )

                    try:
                        remaining = phase_deadline - time.monotonic()
                        if remaining <= 0:
                            raise ValueError(phase + " time budget exhausted")
                        step["returncode"] = execute(
                            spec,
                            frozen["environment"],
                            ROOT,
                            attempt / f"{phase}-{i:02}.log",
                            remaining,
                            started,
                        )
                    finally:
                        record["pid"] = None
                        step["finished"] = now()
                        atomic(attempt / "execution.json", receipt)
                        atomic(path / "state.json", state)
                    event(
                        path,
                        "command_completed",
                        feature=key,
                        phase=phase,
                        returncode=step["returncode"],
                    )
                    if step["returncode"] and not (
                        original.get("optional")
                        and phase in {"prepare", "run", "observe"}
                    ):
                        raise ValueError(
                            f"{phase} command {i} exited {step['returncode']}"
                        )

            failure, cleanup_uncertain = None, False
            try:
                for phase in ("prepare", "run", "observe"):
                    phase_run(phase, deadline)
            except (
                ValueError,
                OSError,
                subprocess.TimeoutExpired,
                KeyboardInterrupt,
            ) as exc:
                failure = str(exc) or "interrupted"
                # A local SSH/container/kubectl error does not tell us whether the
                # remote workload stopped. A successful cleanup command is not an
                # independent absence observation; require reconciliation first.
                cleanup_uncertain = bool(task.get("resources")) or any(
                    spec.get("executor", "local") != "local"
                    for phase in ("prepare", "run")
                    for spec in task.get(phase, [])
                )
            finally:
                try:
                    # One bounded cleanup budget for all commands, not one per command.
                    phase_run("cleanup", time.monotonic() + timeout)
                except (
                    ValueError,
                    OSError,
                    subprocess.TimeoutExpired,
                    KeyboardInterrupt,
                ) as exc:
                    failure = f"{failure or ''}; cleanup: {exc}"
                    cleanup_uncertain = True
            if task.get("resources"):
                from adapter.workflow.executors.resources import observe

                observation = observe(path, key, attempt.name)
                atomic(attempt / "resource-observation.json", observation)
                if not observation["owned_resources_absent"]:
                    cleanup_uncertain = True
                    failure = (
                        failure or ""
                    ) + "; owned resources remain or absence could not be proven"
                elif cleanup_uncertain:
                    cleanup_uncertain = False
            if not failure:
                try:
                    phase_run("verify", deadline)
                    check_freshness(frozen, [key])
                except (
                    ValueError,
                    OSError,
                    subprocess.TimeoutExpired,
                    KeyboardInterrupt,
                ) as exc:
                    failure = str(exc) or "verification interrupted"
            # Snapshot only after verifier exited, so its output/log can no longer race hashing.
            for file in attempt.rglob("*"):
                if file.is_file() and file.name != "execution.json":
                    if file.is_symlink():
                        failure = "Attempt evidence must not be a symlink"
                        continue
                    receipt["files"][str(file.relative_to(attempt))] = digest(file)
            receipt["completed"] = not failure
            atomic(attempt / "execution.json", receipt)
            record["execution_sha256"] = digest(attempt / "execution.json")
            if failure:
                item.update(
                    task="failed",
                    test="failed",
                    qualification="blocked",
                    reason=[failure],
                    reconciliation_required=cleanup_uncertain,
                )
            else:
                try:
                    gate = qualify(
                        read(attempt / "result.json"), frozen, feature, attempt
                    )
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    gate = {
                        "qualification": "pending",
                        "test": "inconclusive",
                        "errors": [str(exc)],
                    }
                atomic(attempt / "qualification.json", gate)
                item.update(
                    task="succeeded",
                    implementation="implemented",
                    test=gate["test"],
                    qualification=gate["qualification"],
                    required_passed=gate.get("required_passed", False),
                    warnings=gate.get("warnings", []),
                    reason=gate["errors"],
                )
            record["finished"] = now()
            atomic(path / "state.json", state)
            event(
                path,
                "attempt_completed",
                feature=key,
                task=item["task"],
                qualification=item["qualification"],
            )
    return state


def reconcile(campaign, feature_id, proof_path):
    path, _, _ = load_campaign(campaign)
    with lock(path / ".lock"):
        path, frozen, state = load_campaign(path)
        item = state["features"][feature_id]
        if item["task"] != "running" and not item.get("reconciliation_required"):
            raise ValueError(
                "Only interrupted attempts or uncertain cleanup need reconciliation"
            )
        record = item["attempts"][-1]
        pid = record.get("pid")
        if pid:
            live = process_identity(pid)
            if live and (
                not record.get("process_identity") or live == record["process_identity"]
            ):
                raise ValueError(
                    "Recorded local process still exists; reconcile its actual state first"
                )
        proof_path = Path(proof_path).resolve()
        proof = read(proof_path)
        if (
            proof.get("identity") != frozen["identity"]
            or proof.get("feature_id") != feature_id
            or proof.get("attempt") != Path(record["path"]).name
            or proof.get("resources") != record.get("resources", [])
            or proof.get("owned_resources_absent") is not True
        ):
            raise ValueError(
                "Reconciliation needs matching identity, attempt, resources and explicit cleanup evidence"
            )
        if not proof.get("artifacts"):
            raise ValueError("Cleanup evidence required")
        target = path / record["path"] / "reconciliation"
        if target.exists():
            raise ValueError("Reconciliation evidence already exists")
        evidence_files = []
        for evidence in proof["artifacts"]:
            file = (proof_path.parent / evidence["path"]).resolve()
            if (
                not file.is_relative_to(proof_path.parent)
                or digest(file) != evidence["sha256"]
            ):
                raise ValueError("Cleanup evidence path or hash mismatch")
            evidence_files.append(file)
        target.mkdir(parents=True)
        for i, file in enumerate(evidence_files):
            name = f"evidence-{i:03}{file.suffix}"
            (target / name).write_bytes(file.read_bytes())
            proof["artifacts"][i]["path"] = name
        atomic(target / "proof.json", proof)
        item.update(
            task="failed",
            test="inconclusive",
            qualification="pending",
            reconciliation_required=False,
            reason=["Interrupted attempt reconciled; next run creates a new attempt"],
        )
        record["reconciliation_sha256"] = digest(target / "proof.json")
        atomic(path / "state.json", state)
        event(path, "reconciled", feature=feature_id)
    return state
