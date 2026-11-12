# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Persistent upgrade control plane; leaves promotion and real deployments explicit."""

import subprocess
from pathlib import Path

import jsonschema

from adapter.workflow.core import (
    ADAPTER,
    ROOT,
    atomic,
    catalog,
    digest,
    event,
    fingerprint,
    identifier,
    lock,
    now,
    read,
)
from adapter.workflow.upgrade import gitops
from adapter.workflow.upgrade.analysis import analyze
from adapter.workflow.upgrade.images import validate_receipt

RESULTS = ADAPTER / "results/workflow/upstream"
SCHEMA = ADAPTER / "contracts/schemas/upgrade-plan.schema.json"


def validate_plan(plan):
    jsonschema.validate(plan, read(SCHEMA))
    identifier(plan["id"])
    for platform in plan["platforms"]:
        identifier(platform)
    return plan


def initialize(repo, plan_file, name, features=None):
    repo = Path(repo).resolve()
    if features is None and repo != ROOT:
        raise ValueError(
            "Run the workflow from the selected repository so its contracts match"
        )
    plan = validate_plan(read(plan_file))
    destination = RESULTS / identifier(name)
    if destination.exists():
        raise ValueError(
            "Session exists; use status/resume, never overwrite a frozen session"
        )
    if not gitops.clean(repo):
        raise ValueError(
            "Commit workflow and source edits first; the upgrade freezes a clean HEYI commit"
        )
    refs = {
        k: gitops.revision(repo, plan["refs"][k]) for k in ("base", "heyi", "target")
    }
    if refs["target"] != plan["target_commit"]:
        raise ValueError("Target tag differs from the reviewed official release commit")
    if refs["heyi"] != gitops.revision(repo, "HEAD"):
        raise ValueError(
            "Initialize from the selected HEYI checkout so contracts match the source"
        )
    if not gitops.ancestor(repo, refs["base"], refs["heyi"]):
        raise ValueError("Old upstream base must be an ancestor of HEYI")
    if gitops.ancestor(repo, refs["target"], refs["base"]):
        raise ValueError("Target is the old upstream baseline or older")
    # Official maintenance releases can diverge; the comparison base is not
    # necessarily Git's merge base. Preserve both and let three-way merge use Git's.
    merge_base = gitops.git(
        repo, "merge-base", refs["heyi"], refs["target"]
    ).stdout.strip()
    features = features if features is not None else catalog()
    if set(plan["mandatory_features"]) - set(features):
        raise ValueError("Unknown mandatory features")
    stacks = {
        p: read(repo / "container/platforms" / (p + ".json")) for p in plan["platforms"]
    }
    frozen = {
        "schema_version": 1,
        "plan": plan,
        "commits": refs,
        "merge_base": merge_base,
        "features": features,
        "stacks": stacks,
        "repository": str(repo),
        "created": now(),
    }
    frozen["identity"] = fingerprint(frozen)
    destination.mkdir(parents=True)
    atomic(destination / "lock.json", frozen)
    atomic(destination / "analysis.json", analyze(repo, frozen))
    atomic(
        destination / "state.json",
        {
            "identity": frozen["identity"],
            "images": {},
            "candidate": None,
            "reviews": {},
            "upstream_review": None,
            "checks": [],
            "campaigns": {},
            "comparisons": {},
            "deliveries": {},
        },
    )
    event(destination, "initialized", commits=refs)
    return destination


def load(path):
    path = Path(path).resolve()
    frozen, state = read(path / "lock.json"), read(path / "state.json")
    identity = frozen.pop("identity")
    if fingerprint(frozen) != identity or state["identity"] != identity:
        raise ValueError("Upgrade lock changed; create a new session")
    frozen["identity"] = identity
    parent = frozen.get("replanned_from")
    if parent and (
        digest(path / "parent-lock.json") != parent["lock_sha256"]
        or digest(path / "parent-state.json") != parent["state_sha256"]
    ):
        raise ValueError("Replanned session parent snapshot changed")
    # Analysis is derived from immutable Git trees, never trust edited impact or ledger JSON.
    expected = analyze(frozen["repository"], frozen)
    if read(path / "analysis.json") != expected:
        raise ValueError("Impact analysis changed; frozen scope cannot be weakened")
    return path, frozen, state, expected


def save(path, state, kind, **fields):
    atomic(path / "state.json", state)
    event(path, kind, **fields)


def bind_image(session, platform, receipt_file):
    with lock(Path(session) / ".lock"):
        path, frozen, state, _ = load(session)
        receipt = read(receipt_file)
        validate_receipt(frozen["plan"]["platforms"][platform], receipt)
        if platform in state["images"] and state["images"][platform] != receipt:
            raise ValueError(
                "Image already frozen; create a new upgrade session to change it"
            )
        state["images"][platform] = receipt
        save(path, state, "image_bound", platform=platform)
        return receipt


def candidate(frozen, state, require_clean=True):
    c = state.get("candidate")
    if not c:
        raise ValueError("Prepare an isolated candidate first")
    tree = gitops.owned_worktree(frozen["repository"], c["path"], c["branch"])
    if require_clean and not gitops.clean(tree):
        raise ValueError(
            "Candidate has uncommitted changes/conflicts; finish and commit before verification"
        )
    head = gitops.revision(tree, "HEAD")
    if require_clean and not all(
        gitops.ancestor(tree, frozen["commits"][k], head) for k in ("heyi", "target")
    ):
        raise ValueError("Candidate does not contain both frozen histories")
    return tree, head


def prepare(session):
    with lock(Path(session) / ".lock"):
        path, frozen, state, _ = load(session)
        if set(state["images"]) != set(frozen["plan"]["platforms"]):
            raise ValueError(
                "All requested image receipts are required before adaptation"
            )
        repo = Path(frozen["repository"])
        c = state.get("candidate")
        if c:
            tree, head = candidate(frozen, state, require_clean=False)
            return {
                "path": str(tree),
                "head": head,
                "message": "Existing candidate retained; inspect conflicts before resuming",
            }
        branch = "upgrade/" + identifier(path.name)
        tree = repo / ".worktrees" / identifier(path.name)
        if (
            tree.exists()
            or gitops.git(
                repo, "show-ref", "--verify", "refs/heads/" + branch, check=False
            ).returncode
            == 0
        ):
            raise ValueError(
                "Candidate path/branch already exists but is not owned by this session"
            )
        gitops.git(
            repo, "worktree", "add", "-b", branch, str(tree), frozen["commits"]["heyi"]
        )
        state["candidate"] = {
            "path": str(tree),
            "branch": branch,
            "status": "merge_pending",
        }
        save(path, state, "candidate_created")
        # No autocommit: a clean textual merge is still unreviewed. Never resolve with ours/theirs.
        result = gitops.git(
            tree,
            "merge",
            "--no-ff",
            "--no-commit",
            frozen["commits"]["target"],
            check=False,
        )
        (path / "merge.log").write_text(result.stdout + result.stderr)
        conflicts = gitops.git(
            tree, "diff", "--name-only", "--diff-filter=U"
        ).stdout.splitlines()
        state["candidate"].update(
            status="conflicts"
            if conflicts
            else ("merged_uncommitted" if result.returncode == 0 else "failed"),
            conflicts=conflicts,
            returncode=result.returncode,
        )
        save(path, state, "merge_attempted", returncode=result.returncode)
        return state["candidate"]


def review(session, item, disposition, reason, reviewer):
    with lock(Path(session) / ".lock"):
        path, frozen, state, analysis = load(session)
        _, head = candidate(frozen, state)
        if not reason.strip() or not reviewer.strip():
            raise ValueError("Reviewer and semantic rationale required")
        record = {
            "disposition": disposition,
            "reason": reason,
            "reviewer": reviewer,
            "commit": head,
            "time": now(),
        }
        if item == "upstream":
            if disposition != "reviewed":
                raise ValueError("Use reviewed for new upstream capability review")
            state["upstream_review"] = record
        else:
            if item not in {x["id"] for x in analysis["ledger"]}:
                raise ValueError("Unknown ledger ID")
            if disposition not in {
                "retain",
                "rewrite",
                "upstream_replaced",
                "remove",
                "blocked",
            }:
                raise ValueError("Unknown migration disposition")
            state["reviews"][item] = record
        save(path, state, "review_recorded", item=item, review=record)
        return record


def review_batch(session, input_file, reviewer):
    """Record explicit per-file decisions atomically, after checking every source hash."""
    if not reviewer.strip():
        raise ValueError("Reviewer required")
    with lock(Path(session) / ".lock"):
        path, frozen, state, analysis = load(session)
        tree, head = candidate(frozen, state)
        data = read(input_file)
        if data.get("candidate") != head:
            raise ValueError("Review batch targets a different candidate")
        entries = data.get("records")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Review batch requires explicit per-file records")
        ledger = {entry["id"]: entry["path"] for entry in analysis["ledger"]}
        records = {}
        for entry in entries:
            key = entry["id"]
            if key not in ledger or key in records or entry["path"] != ledger[key]:
                raise ValueError("Unknown, duplicate or mismatched review path")
            disposition, reason = entry["disposition"], entry["reason"]
            if (
                disposition
                not in {"retain", "rewrite", "upstream_replaced", "remove", "blocked"}
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise ValueError("Explicit disposition and semantic rationale required")
            file = tree / ledger[key]
            checksum = entry["sha256"]
            if file.is_file():
                if checksum != digest(file):
                    raise ValueError("Reviewed source hash changed: " + ledger[key])
            elif checksum is not None or disposition not in {
                "remove",
                "upstream_replaced",
            }:
                raise ValueError(
                    "Missing source requires an explicit removal/replacement review"
                )
            records[key] = {
                "disposition": disposition,
                "reason": reason,
                "reviewer": reviewer,
                "commit": head,
                "time": now(),
                "source_sha256": checksum,
            }
        # No partial writes if any entry fails. Reject a concurrently moved HEAD.
        if candidate(frozen, state)[1] != head:
            raise ValueError("Candidate moved during batch review")
        receipt = path / "reviews" / (fingerprint(data) + ".json")
        if receipt.exists() and read(receipt) != data:
            raise ValueError("Existing review receipt changed")
        atomic(receipt, data)
        for record in records.values():
            record["batch"] = str(receipt)
            record["batch_sha256"] = digest(receipt)
        state["reviews"].update(records)
        save(
            path,
            state,
            "review_batch_recorded",
            receipt=str(receipt),
            items=sorted(records),
        )
        return {
            "candidate": head,
            "reviewed_items": len(records),
            "receipt": str(receipt),
        }


def check(session):
    with lock(Path(session) / ".lock"):
        path, frozen, state, _ = load(session)
        tree, head = candidate(frozen, state)
        attempt = path / "checks" / f"{len(state['checks']) + 1:03}"
        attempt.mkdir(parents=True)
        record = {
            "commit": head,
            "status": "running",
            "path": str(attempt),
            "commands": [],
            "started": now(),
        }
        state["checks"].append(record)
        save(path, state, "checks_started")
        try:
            for i, argv in enumerate(frozen["plan"]["checks"]):
                with (attempt / f"{i}.log").open("w") as output:
                    p = subprocess.run(
                        argv,
                        check=False,
                        cwd=tree,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        timeout=frozen["plan"]["check_timeout_seconds"],
                    )
                record["commands"].append(
                    {
                        "argv": argv,
                        "returncode": p.returncode,
                        "log_sha256": digest(attempt / f"{i}.log"),
                    }
                )
                if p.returncode:
                    raise ValueError(
                        "Static check failed; inspect " + str(attempt / f"{i}.log")
                    )
            if not gitops.clean(tree) or gitops.revision(tree, "HEAD") != head:
                raise ValueError(
                    "Checks modified source; commit and repeat verification"
                )
            record["status"] = "passed"
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            record.update(status="failed", error=str(exc))
        record["finished"] = now()
        save(path, state, "checks_finished", status=record["status"])
        return record


def handoff(session, platform, environment, chip):
    path, frozen, state, analysis = load(session)
    tree, head = candidate(frozen, state)
    identifier(chip)
    if platform not in frozen["plan"]["platforms"]:
        raise ValueError("Unknown platform")
    out = path / "handoffs" / platform / head
    if out.exists():
        raise ValueError("Handoff exists; retain it or use a new candidate commit")
    out.mkdir(parents=True)
    scope = {
        "schema_version": 1,
        "features": {
            k: {"disposition": "required"} for k in analysis["required_features"]
        },
    }
    scope["features"].update(analysis.get("deferred_features", {}))
    alternatives = {
        k: f["dependencies_any_of"]
        for k, f in frozen["features"].items()
        if f.get("dependencies_any_of")
    }
    atomic(out / "scope.proposed.json", scope)
    campaign = f"adapter/results/{platform}/{chip}/{path.name}-{head[:12]}"
    commands = [
        [
            "python3",
            "-m",
            "adapter.workflow",
            "init",
            "--platform",
            platform,
            "--chip",
            chip,
            "--campaign",
            campaign,
            "--environment",
            str(Path(environment).resolve()),
            "--scope",
            str(out / "scope.proposed.json"),
        ],
        ["python3", "-m", "adapter.workflow", "doctor", "--campaign", campaign],
        ["python3", "-m", "adapter.workflow", "compile", "--campaign", campaign],
        [
            "python3",
            "-m",
            "adapter.workflow",
            "automate",
            "--campaign",
            campaign,
            "--harness",
            "codex",
            "--model",
            "<explicit-model>",
            "--max-cycles",
            "3",
            "--wall-seconds",
            "3600",
        ],
    ]
    result = {
        "cwd": str(tree),
        "candidate": head,
        "platform": platform,
        "campaign": campaign,
        "commands": commands,
        "dependency_choices_required": alternatives,
        "instructions": "Review scope/any-of choices and actual environment. Doctor precedes execution. Use --harness claude/openai with configured model if desired. AI edits invalidate campaigns; refreeze after committing. No commands executed.",
    }
    atomic(out / "handoff.json", result)
    return result


def attach_campaign(session, platform, campaign):
    with lock(Path(session) / ".lock"):
        path, frozen, state, analysis = load(session)
        tree, head = candidate(frozen, state)
        campaign = Path(campaign).resolve()
        if not campaign.is_relative_to(tree / "adapter/results" / platform):
            raise ValueError("Campaign must belong to this candidate and platform")
        cf = read(campaign / "lock.json")
        target, receipt = (
            frozen["plan"]["platforms"][platform],
            state["images"][platform],
        )
        if cf["source"]["head"] != head or cf["platform"] != platform:
            raise ValueError("Campaign source/platform mismatch")
        if (
            cf["stack"]["versions"]["vllm"].split("+")[0] != target["vllm"]
            or cf["stack"]["base_image"] != receipt["repo_digest"]
        ):
            raise ValueError(
                "Candidate canonical stack must match frozen image/version targets"
            )
        if set(analysis["required_features"]) - set(cf["required"]):
            raise ValueError(
                "Campaign omits impacted/mandatory features; explicit new scope plan is needed for deferral"
            )
        for key, declaration in analysis.get("deferred_features", {}).items():
            if cf.get("scope", {}).get("features", {}).get(key) != declaration:
                raise ValueError(
                    "Campaign must retain the exact accepted deferral: " + key
                )
        # Do not allow feature-level coverage to conceal skipped individual cases or altered contracts.
        for key in analysis["required_features"]:
            expected = frozen["features"][key]
            actual = cf["features"][key]
            if (
                actual["revision"] != expected["revision"]
                or actual["cases"] != expected["cases"]
                or actual.get("contract_sha256") != expected.get("contract_sha256")
            ):
                raise ValueError(
                    "Feature/case contract changed; replan the upgrade explicitly"
                )
            if any(
                v["disposition"] != "required"
                for v in actual.get("case_scope", {}).values()
            ):
                raise ValueError(
                    "Upgrade campaign cannot silently weaken required cases"
                )
        output = campaign / (
            "upgrade-release-"
            + str(len(list(campaign.glob("upgrade-release-*.json"))) + 1)
            + ".json"
        )
        result = subprocess.run(
            [
                "python3",
                "-m",
                "adapter.workflow",
                "release",
                "--campaign",
                str(campaign),
                "--output",
                str(output),
            ],
            cwd=tree,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode:
            raise ValueError(
                "Candidate evidence audit failed: " + result.stderr[-2000:]
            )
        report = read(output)
        if report["status"] != "verified_declared_scope":
            raise ValueError("Campaign is not qualified")
        state["campaigns"][platform] = {
            "path": str(campaign),
            "commit": head,
            "report": str(output),
            "sha256": digest(output),
        }
        save(path, state, "campaign_attached", platform=platform)
        return state["campaigns"][platform]


def status(session):
    _path, frozen, state, analysis = load(session)
    blockers = []
    head = None
    try:
        _, head = candidate(frozen, state)
    except ValueError as exc:
        blockers.append(str(exc))
    if set(state["images"]) != set(frozen["plan"]["platforms"]):
        blockers.append("images_not_ready")
    pending = [
        x["id"]
        for x in analysis["ledger"]
        if state["reviews"].get(x["id"], {}).get("commit") != head
        or state["reviews"].get(x["id"], {}).get("disposition") in {None, "blocked"}
    ]
    if pending:
        blockers.append(f"migration_reviews_pending:{len(pending)}")
    batch_receipts = {
        (r["batch"], r["batch_sha256"])
        for r in state["reviews"].values()
        if r.get("commit") == head and r.get("batch")
    }
    if any(
        not Path(file).resolve().is_relative_to((_path / "reviews").resolve())
        or not Path(file).is_file()
        or digest(file) != checksum
        for file, checksum in batch_receipts
    ):
        blockers.append("review_batch_evidence_changed")
    if not head or (state.get("upstream_review") or {}).get("commit") != head:
        blockers.append("upstream_semantic_review_pending")
    current_checks = [c for c in state["checks"] if c["commit"] == head]
    if not current_checks or current_checks[-1]["status"] != "passed":
        blockers.append("candidate_checks_pending")
    else:
        for i, c in enumerate(current_checks[-1]["commands"]):
            file = Path(current_checks[-1]["path"]) / f"{i}.log"
            if not file.is_file() or digest(file) != c["log_sha256"]:
                blockers.append("check_evidence_changed")
    for platform in frozen["plan"]["platforms"]:
        r = state["campaigns"].get(platform, {})
        if r.get("commit") != head or not Path(r.get("report", "")).is_file():
            blockers.append(platform + ":fresh_M01_M10_campaign_pending")
        elif digest(r["report"]) != r["sha256"]:
            blockers.append(platform + ":campaign_report_changed")
        comparison = state["comparisons"].get(platform, {})
        if comparison.get("commit") != head or comparison.get("status") != "passed":
            blockers.append(platform + ":paired_comparison_pending")
        else:
            for file, expected in comparison["artifacts"].items():
                if not Path(file).is_file() or digest(file) != expected:
                    blockers.append(platform + ":comparison_evidence_changed")
        delivery = state.get("deliveries", {}).get(platform, {})
        if (
            delivery.get("commit") != head
            or not Path(delivery.get("path", "")).is_file()
        ):
            blockers.append(platform + ":sealed_M10_delivery_pending")
        elif digest(delivery["path"]) != delivery["sha256"]:
            blockers.append(platform + ":delivery_record_changed")
    return {
        "identity": frozen["identity"],
        "candidate": head,
        "status": "ready_for_promotion_review" if not blockers else "pending",
        "blockers": blockers,
        "required_features": analysis["required_features"],
        "deferred_features": analysis.get("deferred_features", {}),
        "ledger_items": len(analysis["ledger"]),
        "next": "Prepare images, candidate, reviews, checks, M01–M10 campaigns and paired comparisons; no automatic publish",
    }
