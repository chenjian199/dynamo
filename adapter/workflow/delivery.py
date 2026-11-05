# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resume-safe orchestration of the existing M02/M10 offline delivery tools."""

from adapter.workflow.core import (
    ROOT,
    atomic,
    digest,
    fingerprint,
    load_campaign,
    lock,
    now,
    read,
)
from adapter.workflow.executors.commands import execute, substitute
from adapter.workflow.state.runner import check_freshness

BASE = "adapter/modules/m10-validation/common/offline/"
PHASES = {
    "build": [
        "container/platform-build.py",
        "adapter/modules/m10-validation/common/images/build-derived.sh",
    ],
    "dependencies": [BASE + "dependency-inventory.py"],
    "export": [BASE + "export-images.py"],
    "verify_archive": [BASE + "verify-images.py"],
    "import": [BASE + "import.sh"],
    "smoke": [],
    "seal": [BASE + "bundle.py"],
}


def validate_smoke(root, plan, frozen):
    report = read(root / plan["smoke_report"])
    if (
        report.get("identity") != frozen["identity"]
        or report.get("stack_sha256") != frozen["stack_sha256"]
    ):
        raise ValueError("Import smoke report belongs to different inputs")
    if report.get("versions") != frozen["stack"]["versions"]:
        raise ValueError("Imported runtime version mismatch")
    if not report.get("requests") or not all(
        r.get("http_status") == 200
        and r.get("output_tokens", 0) > 0
        and r.get("actual") == r.get("expected")
        and r.get("actual")
        for r in report["requests"]
    ):
        raise ValueError("Imported endpoint smoke/parity failed")
    images = report.get("images", [])
    if {i["role"] for i in images} != {"runtime", "dev", "tools", "operator"}:
        raise ValueError("Missing imported image role")
    if any(
        not i.get("expected_id") or i.get("expected_id") != i.get("observed_id")
        for i in images
    ):
        raise ValueError("Imported image identity mismatch")
    return report


def deliver(campaign, plan_path):
    path, frozen, _ = load_campaign(campaign)
    if not frozen["environment"].get("execution_enabled"):
        raise ValueError("Delivery execution is disabled")
    plan = read(plan_path)
    if set(plan.get("phases", {})) != set(PHASES):
        raise ValueError(
            "Delivery requires build/dependencies/export/verify_archive/import/smoke/seal"
        )
    root = (path / plan["directory"]).resolve()
    if not root.is_relative_to(path) or root == path:
        raise ValueError("Delivery directory must be inside this campaign")
    for phase, allowed in PHASES.items():
        specs = plan["phases"][phase]
        if not specs:
            raise ValueError("Empty delivery phase: " + phase)
        if allowed and not any(
            any(
                str(ROOT / script) in spec["argv"] or "{repo}/" + script in spec["argv"]
                for script in allowed
            )
            for spec in specs
        ):
            raise ValueError("Use canonical M02/M10 tool for " + phase)
    for field in ("smoke_report", "archive"):
        if not (root / plan[field]).resolve().is_relative_to(root):
            raise ValueError("Delivery artifact escapes campaign directory")
    root.mkdir(parents=True, exist_ok=True)
    with lock(root / ".lock"):
        file = root / "delivery-state.json"
        state = (
            read(file)
            if file.exists()
            else {
                "identity": frozen["identity"],
                "plan_sha256": fingerprint(plan),
                "plan": plan,
                "phases": {},
                "status": "running",
            }
        )
        if state["identity"] != frozen["identity"] or state[
            "plan_sha256"
        ] != fingerprint(plan):
            raise ValueError("Delivery inputs changed; use a new campaign")
        check_freshness(frozen)
        for phase, specs in plan["phases"].items():
            if list(plan["phases"]) != list(PHASES):
                raise ValueError("Delivery phases must execute in the canonical order")
            previous = state["phases"].get(phase)
            if previous:
                if previous["status"] != "completed":
                    raise ValueError(
                        "Interrupted/failed delivery phase: inspect owned resources and outputs; start a new delivery directory after reconciliation"
                    )
                for name, checksum in previous.get("artifacts", {}).items():
                    if digest(root / name) != checksum:
                        raise ValueError("Delivery evidence changed: " + name)
                continue
            entry = {"status": "running", "started": now(), "commands": specs}
            state["phases"][phase] = entry
            atomic(file, state)
            try:
                for i, spec in enumerate(specs):
                    expanded = substitute(
                        spec,
                        frozen["environment"],
                        ROOT,
                        path / "features/M10-F04/delivery",
                        frozen["identity"],
                    )
                    expanded["argv"] = [
                        v.replace("{delivery}", str(root)) for v in expanded["argv"]
                    ]
                    code = execute(
                        expanded,
                        frozen["environment"],
                        ROOT,
                        root / f"{phase}-{i:02}.log",
                        frozen["environment"]["budgets"]["task_timeout_seconds"],
                        lambda pid, phase=phase: atomic(
                            root / "process.json", {"pid": pid, "phase": phase}
                        ),
                    )
                    if code:
                        raise ValueError(f"{phase} exited {code}")
                if phase == "smoke":
                    validate_smoke(root, plan, frozen)
                outputs = plan.get("outputs", {}).get(phase, [])
                if not outputs:
                    raise ValueError(
                        "Every delivery phase requires hashed output evidence"
                    )
                artifacts = {}
                for name in outputs:
                    source = (root / name).resolve()
                    if not source.is_relative_to(root) or source.is_symlink():
                        raise ValueError("Unsafe delivery output")
                    artifacts[name] = digest(source)
                entry.update(status="completed", artifacts=artifacts, finished=now())
            except BaseException as exc:
                entry.update(status="failed", error=str(exc))
                atomic(file, state)
                raise
            atomic(file, state)
        validate_smoke(root, plan, frozen)
        archive = root / plan["archive"]
        state.update(
            status="artifacts_sealed",
            archive_sha256=digest(archive),
            scope=frozen["scope"],
            lock_sha256=digest(path / "lock.json"),
            qualification="Run M10 feature gates and release; archive existence alone is not platform certification",
        )
        atomic(file, state)
    return state
