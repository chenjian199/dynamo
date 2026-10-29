#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Count baseline-to-working-tree changes without altering the real Git index.

Includes tracked changes and nonignored adapter files. Other untracked paths,
including reference/, are excluded. Results and generated files are separate
from source code. Physical line counts include comments and blank lines.
"""

import collections
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "v1.5.1"
REPORT = "adapter/workload.md"
SOURCE = {".py", ".rs", ".go", ".sh", ".c", ".cpp", ".cu", ".h", ".hpp"}


def category(path):
    p = Path(path)
    if (
        "results" in p.parts
        or p.suffix in {".log", ".gz", ".zip", ".patch", ".svg", ".webm"}
        or p.name.endswith(".lock")
        or p.name in {"CODEOWNERS", "CONTRIBUTORS.md"}
        or "-results." in p.name
    ):
        return "artifacts"
    if p.suffix in SOURCE:
        if path.startswith(("adapter/", "tests/", "benchmarks/")) or any(
            "test" in part.lower() for part in p.parts
        ):
            return "validation_code"
        if path.startswith(("container/", "scripts/", ".github/", ".agents/")):
            return "tooling_code"
        return "production_code"
    if p.suffix in {".md", ".license"} or p.name == "AGENTS.md":
        return "docs_licenses"
    if p.suffix in {".yaml", ".yml", ".toml"} or "Dockerfile" in p.name:
        return "build_config"
    if path.startswith(("container/", "deploy/")) and p.suffix == ".json":
        return "build_config"
    return "other_data"


def main():
    with tempfile.TemporaryDirectory(prefix="dynamo-change-stats-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))

        def git(*args):
            return subprocess.check_output(["git", *args], cwd=ROOT, env=env).decode()

        git("read-tree", "HEAD")
        git("add", "-u")
        git("add", "--", "adapter")
        tree = git("write-tree").strip()
        entries = iter(git("diff", "--numstat", "-z", "-M", BASE, tree).split("\0"))
        rows = []
        base_paths = set(git("ls-tree", "-r", "--name-only", BASE).splitlines())
        for entry in entries:
            if not entry:
                continue
            added, deleted, path = entry.split("\t", 2)
            old = path
            if not path:  # Git encodes rename source and destination separately.
                old, path = next(entries), next(entries)
            if path == REPORT:
                continue  # Avoid self-referential report counts.
            rows.append(
                {
                    "path": path,
                    "category": category(path),
                    "new_file": old not in base_paths,
                    "binary": added == "-",
                    "added": int(added) if added != "-" else 0,
                    "deleted": int(deleted) if deleted != "-" else 0,
                }
            )
        totals = collections.defaultdict(
            lambda: {
                "files": 0,
                "new_files": 0,
                "added": 0,
                "deleted": 0,
                "binary_files": 0,
            }
        )
        for row in rows:
            result = totals[row["category"]]
            result["files"] += 1
            result["new_files"] += int(row["new_file"])
            result["binary_files"] += int(row["binary"])
            result["added"] += row["added"]
            result["deleted"] += row["deleted"]
        print(
            json.dumps(
                {
                    "baseline": git("rev-parse", BASE).strip(),
                    "head": git("rev-parse", "HEAD").strip(),
                    "commits_since_baseline": int(
                        git("rev-list", "--count", BASE + "..HEAD")
                    ),
                    "scope": "tracked worktree changes plus nonignored adapter files; excludes "
                    + REPORT,
                    "totals": dict(totals),
                    "files": rows,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
