# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""python -m adapter.workflow: persistent feature adaptation workflows."""

import argparse
import json
import sys
from pathlib import Path

from adapter.workflow import operations
from adapter.workflow.core import (
    ADAPTER,
    ROOT,
    atomic,
    catalog,
    fingerprint,
    freshness,
    identifier,
    init_campaign,
    load_campaign,
    platform_stack,
    read,
    source_identity,
)
from adapter.workflow.harnesses.agent import handoff
from adapter.workflow.identity import freshness_map
from adapter.workflow.preflight import doctor
from adapter.workflow.reporting.reports import (
    catalog_reports,
    evidence_integrity,
    release,
    report_campaign,
)
from adapter.workflow.scope import accepted
from adapter.workflow.state.plan import seal_plan
from adapter.workflow.state.runner import reconcile, run


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--campaign", type=Path, required=True)
    init.add_argument("--platform", required=True)
    init.add_argument("--chip", required=True)
    init.add_argument("--environment", type=Path, required=True)
    init.add_argument("--features", nargs="+")
    init.add_argument("--scope", type=Path)
    operations.parsers(sub)
    onboard = sub.add_parser("onboard")
    onboard.add_argument("--platform", required=True)
    onboard.add_argument("--stack-profile", required=True)
    onboard.add_argument("--resource-key", required=True)
    for name in [
        "doctor",
        "plan",
        "status",
        "run",
        "resume",
        "report",
        "release",
        "agent",
        "reconcile",
        "seal-plan",
    ]:
        c = sub.add_parser(name)
        c.add_argument("--campaign", type=Path, required=name != "report")
        if name in ["run", "resume"]:
            c.add_argument("--feature")
        if name == "report":
            c.add_argument("--catalog", action="store_true")
            c.add_argument("--check", action="store_true")
        if name == "release":
            c.add_argument("--output", type=Path, required=True)
        if name == "reconcile":
            c.add_argument("--feature", required=True)
            c.add_argument("--proof", type=Path, required=True)
        if name == "agent":
            c.add_argument("--feature", required=True)
            c.add_argument(
                "--harness", choices=["codex", "claude", "openai"], default="codex"
            )
            c.add_argument("--model")
            c.add_argument("--invoke", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.command == "init":
            result = {
                "campaign": str(
                    init_campaign(
                        args.campaign,
                        args.platform,
                        args.chip,
                        args.environment,
                        args.features,
                        read(args.scope) if args.scope else None,
                    )
                )
            }
        elif args.command in operations.NAMES:
            result = operations.dispatch(args)
        elif args.command == "onboard":
            identifier(args.platform)
            target = ADAPTER / "platforms" / args.platform / "platform.yaml"
            if target.exists():
                raise ValueError("Platform already exists")
            stack, _ = platform_stack(args.platform, args.stack_profile)
            atomic(
                target,
                {
                    "schema_version": 1,
                    "id": args.platform,
                    "stack_profile": str(stack.relative_to(ROOT)),
                    "resource_key": args.resource_key,
                    "target_template": "adapter/environments/examples/cluster.yaml",
                    "policy": "adapter/contracts/policy.yaml",
                },
            )
            target.with_name(target.name + ".license").write_text(
                "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\n"
            )
            result = {
                "platform": args.platform,
                "features": list(catalog()),
                "next": "Describe nodes and immutable assets, then init/doctor/plan",
            }
        elif args.command == "doctor":
            result = doctor(args.campaign)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 2
        elif args.command == "seal-plan":
            result = seal_plan(args.campaign)
        elif args.command in ["run", "resume"]:
            result = run(args.campaign, args.feature)
        elif args.command == "agent":
            result = handoff(
                args.campaign, args.feature, args.harness, args.model, args.invoke
            )
        elif args.command == "reconcile":
            result = reconcile(args.campaign, args.feature, args.proof)
        elif args.command == "release":
            result = release(args.campaign, args.output)
        elif args.command == "report":
            result = (
                catalog_reports(args.check)
                if args.catalog
                else {"report": report_campaign(args.campaign)}
            )
        else:
            path, frozen, state = load_campaign(args.campaign)
            state = dict(
                state,
                freshness=freshness(frozen),
                feature_freshness=freshness_map(frozen, source_identity(), fingerprint),
                evidence_integrity=evidence_integrity(path, frozen, state),
            )
            result = (
                state
                if args.command == "status"
                else {
                    "identity": frozen["identity"],
                    "ordered_features": frozen["planned"],
                    "required": frozen["required"],
                    "requirements": {
                        k: v["requirements"] for k, v in frozen["features"].items()
                    },
                    "tasks": str(path / "tasks.json"),
                }
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command in ["run", "resume"]:
            selected = [args.feature] if args.feature else list(result["features"])
            return (
                0 if all(accepted(result["features"][key]) for key in selected) else 2
            )
        if args.command == "automate":
            return 0 if result.get("status") == "qualified_declared_scope" else 2
        if args.command == "compile":
            return 2 if any(v["required"] for v in result["missing"]) else 0
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"adapter: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
