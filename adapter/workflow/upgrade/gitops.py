# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read Git identities and merge only in an explicitly owned upgrade worktree."""

import subprocess
from pathlib import Path


def git(repo, *args, check=True):
    p = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )
    if check and p.returncode:
        raise ValueError(p.stderr.strip() or p.stdout.strip() or "Git failed")
    return p


def revision(repo, ref):
    return git(
        repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"
    ).stdout.strip()


def ancestor(repo, before, after):
    return (
        git(repo, "merge-base", "--is-ancestor", before, after, check=False).returncode
        == 0
    )


def changes(repo, before, after):
    # --no-renames intentionally records deletion and addition: neither endpoint is lost.
    raw = git(
        repo, "diff", "--name-status", "--no-renames", "-z", before, after, "--"
    ).stdout.split("\0")
    return [{"change": raw[i], "path": raw[i + 1]} for i in range(0, len(raw) - 1, 2)]


def clean(repo):
    return not git(
        repo,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude)adapter/results/**",
    ).stdout.strip()


def owned_worktree(repo, path, branch):
    path = Path(path).resolve()
    blocks = git(repo, "worktree", "list", "--porcelain").stdout.split("\n\n")
    for block in blocks:
        rows = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        if (
            rows.get("worktree") == str(path)
            and rows.get("branch") == "refs/heads/" + branch
        ):
            return path
    raise ValueError("Worktree identity/branch changed; refusing to modify it")
