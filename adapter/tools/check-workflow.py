#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check migrated entrypoints, contracts, generated views, and historical hashes."""

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from adapter.workflow.core import catalog
from adapter.workflow.reporting.audit import workflow_audit
from adapter.workflow.reporting.reports import catalog_reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Also reject incomplete agreed workflow scope",
    )
    args = parser.parse_args()
    features = catalog()
    errors = []
    python_files = 0
    shell_files = 0
    artifacts = 0
    catalog_reports(check=True)
    for f in features.values():
        for name in f.get("entrypoints", []):
            if not (ROOT / name).is_file():
                errors.append("Missing entrypoint: " + name)
        for case in f["cases"]:
            if not case["id"].startswith(f["id"] + "-C"):
                errors.append("Mismatched case identity: " + case["id"])
    for p in (ROOT / "adapter").rglob("*"):
        if not p.is_file() or p.is_symlink() or "__pycache__" in p.parts:
            continue
        try:
            text = p.read_text()
        except UnicodeError:
            continue
        if re.search(r"(?<![A-Za-z])(?:adapter/m\d{2}|" + "adapt" + r")/", text):
            errors.append("Obsolete reference: " + str(p.relative_to(ROOT)))
        if "results" not in p.parts and p.suffix == ".py":
            tree = ast.parse(text, filename=str(p))
            python_files += 1
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "with_name"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    expr = ast.unparse(node.func.value)
                    if (
                        expr == "Path(__file__)"
                        and not p.with_name(node.args[0].value).exists()
                    ):
                        errors.append(
                            f"Missing sibling: {p.relative_to(ROOT)} -> {node.args[0].value}"
                        )
        if "results" not in p.parts and p.suffix == ".sh":
            for relative in re.findall(r"\$SCRIPT_DIR((?:/\.\.)+)", text):
                if (p.parent / relative.lstrip("/")).resolve() != ROOT:
                    errors.append(
                        f"Incorrect shell repository root: {p.relative_to(ROOT)}"
                    )
            result = subprocess.run(
                ["bash", "-n", str(p)], capture_output=True, text=True, check=False
            )
            shell_files += 1
            if result.returncode:
                errors.append(result.stderr)
    for p in (ROOT / "adapter/results").glob("*/*/*/evidence-index.json"):
        index = json.loads(p.read_text())
        for record in index["artifacts"]:
            file = (p.parent / record["path"]).resolve()
            if not file.is_relative_to(p.parent.resolve()) or not file.is_file():
                errors.append("Invalid evidence reference: " + str(file))
                continue
            if hashlib.sha256(file.read_bytes()).hexdigest() != record["sha256"]:
                errors.append("Evidence hash mismatch: " + str(file))
            artifacts += 1
    result = {
        "features": len(features),
        "python_files": python_files,
        "shell_files": shell_files,
        "historical_artifacts": artifacts,
        "errors": errors,
        "finalization": workflow_audit(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        return 1
    return 2 if args.require_ready and not result["finalization"]["ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
