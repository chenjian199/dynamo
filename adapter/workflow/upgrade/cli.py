# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Upstream upgrade commands. No merge, pull, deployment or publication is implicit."""

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import jsonschema

from adapter.workflow.core import ROOT, atomic, read
from adapter.workflow.upgrade import gates, session
from adapter.workflow.upgrade.images import probe
from adapter.workflow.upgrade.replan import replan_scope


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="Read official latest stable release metadata only")
    init = sub.add_parser("init")
    init.add_argument("--plan", type=Path, required=True)
    init.add_argument("--name", required=True)
    init.add_argument("--repo", type=Path, default=ROOT)
    validate = sub.add_parser("validate-plan")
    validate.add_argument("--plan", type=Path, required=True)
    replan = sub.add_parser("replan-scope")
    replan.add_argument("--session", type=Path, required=True)
    replan.add_argument("--plan", type=Path, required=True)
    replan.add_argument("--name", required=True)
    replan.add_argument("--reviewer", required=True)
    replan.add_argument("--reason", required=True)
    image = sub.add_parser("probe-image")
    image.add_argument("--plan", type=Path, required=True)
    image.add_argument("--platform", required=True)
    image.add_argument("--image", required=True)
    image.add_argument("--runtime", choices=["docker", "podman"], default="docker")
    image.add_argument("--output", type=Path, required=True)
    for name in [
        "status",
        "prepare",
        "check",
        "bind-image",
        "review",
        "handoff",
        "attach-campaign",
        "compare",
        "attach-delivery",
        "promotion-plan",
    ]:
        p = sub.add_parser(name)
        p.add_argument("--session", type=Path, required=True)
        if name in {
            "bind-image",
            "handoff",
            "attach-campaign",
            "compare",
            "attach-delivery",
        }:
            p.add_argument("--platform", required=True)
        if name == "bind-image":
            p.add_argument("--receipt", type=Path, required=True)
        if name == "review":
            p.add_argument("--item", required=True)
            p.add_argument(
                "--disposition",
                choices=[
                    "retain",
                    "rewrite",
                    "upstream_replaced",
                    "remove",
                    "blocked",
                    "reviewed",
                ],
                required=True,
            )
            p.add_argument("--reason", required=True)
            p.add_argument("--reviewer", required=True)
        if name == "handoff":
            p.add_argument("--environment", type=Path, required=True)
            p.add_argument("--chip", required=True)
        if name == "attach-campaign":
            p.add_argument("--campaign", type=Path, required=True)
        if name in {"compare", "attach-delivery"}:
            p.add_argument("--input", type=Path, required=True)
        if name == "promotion-plan":
            p.add_argument("--rollback", type=Path, required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == "discover":
            url = "https://api.github.com/repos/ai-dynamo/dynamo/releases/latest"
            with urllib.request.urlopen(
                urllib.request.Request(
                    url, headers={"User-Agent": "heyi-adapter-upgrade"}
                ),
                timeout=30,
            ) as response:
                data = json.load(response)
            if data["draft"] or data["prerelease"]:
                raise ValueError("Release is not stable")
            result = {
                k: data[k]
                for k in ["tag_name", "published_at", "html_url", "target_commitish"]
            }
            result["note"] = (
                "Discovery only; an explicit target tag and peeled commit are frozen at init"
            )
        elif a.command == "validate-plan":
            result = {"valid": True, "plan": session.validate_plan(read(a.plan))["id"]}
        elif a.command == "probe-image":
            plan = session.validate_plan(read(a.plan))
            if a.output.exists():
                raise ValueError("Receipt exists; do not overwrite observations")
            result = probe(plan["platforms"][a.platform], a.image, a.runtime)
            atomic(a.output, result)
        elif a.command == "init":
            result = {"session": str(session.initialize(a.repo, a.plan, a.name))}
        elif a.command == "replan-scope":
            result = {
                "session": str(
                    replan_scope(a.session, a.plan, a.name, a.reviewer, a.reason)
                )
            }
        elif a.command == "bind-image":
            result = session.bind_image(a.session, a.platform, a.receipt)
        elif a.command == "review":
            result = session.review(
                a.session, a.item, a.disposition, a.reason, a.reviewer
            )
        elif a.command == "handoff":
            result = session.handoff(a.session, a.platform, a.environment, a.chip)
        elif a.command == "attach-campaign":
            result = session.attach_campaign(a.session, a.platform, a.campaign)
        elif a.command == "compare":
            result = gates.compare(a.session, a.platform, a.input)
        elif a.command == "attach-delivery":
            result = gates.attach_delivery(a.session, a.platform, a.input)
        elif a.command == "promotion-plan":
            result = gates.promotion(a.session, a.rollback)
        else:
            result = getattr(session, a.command)(a.session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if a.command in {"check", "compare"} and result["status"] != "passed":
            return 2
        if a.command == "prepare" and result.get("returncode", 0) != 0:
            return 2
        if a.command == "status" and result["blockers"]:
            return 2
        return 0
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        jsonschema.ValidationError,
        subprocess.TimeoutExpired,
    ) as exc:
        print("adapter upgrade: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
