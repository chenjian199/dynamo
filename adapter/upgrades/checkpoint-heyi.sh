#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Explicitly invoked by the operator; never publishes images or certifies a release.
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
CANDIDATE=2a239c2b500ea7e48c86b7216b9878158db4802a
SOURCE_BRANCH=upgrade/source-1.6.2-ascend-20261111
CANDIDATE_BRANCH=upgrade/dynamo-1.6.2-ascend-20261111
SOURCE_WT="$ROOT/.worktrees/source-1.6.2-ascend-20261111"
CANDIDATE_WT="$SOURCE_WT/.worktrees/dynamo-1.6.2-ascend-20261111"

test "$(git branch --show-current)" = heyi
test "$(git rev-parse "$CANDIDATE_BRANCH")" = "$CANDIDATE"
test -z "$(git ls-files -u)"
test ! -f "$(git rev-parse --git-path MERGE_HEAD)"
for worktree in "$SOURCE_WT" "$CANDIDATE_WT"; do
    git -C "$worktree" diff --quiet
    git -C "$worktree" diff --cached --quiet
done

case "${1:-}" in
merge)
    test "$(git rev-parse HEAD)" = 119c309c9004a0c46dcca96037466444633729c2
    git diff --cached --quiet
    # Check copied evidence without changing frozen manifests or original paths.
    python3 - <<'PY'
import hashlib
import json
from pathlib import Path

record = Path("adapter/results/workflow/upstream/pause-20261113/worktree-evidence-copy.json")
for item in json.loads(record.read_text())["files"]:
    for path in (Path(item["path"]), Path(item["source_worktree"]) / item["path"]):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], path
PY
    git add -- .gitattributes .gitignore \
        adapter/README.md adapter/contracts/schemas/README.md \
        adapter/contracts/schemas/upgrade-plan.schema.json \
        adapter/contracts/schemas/upgrade-plan.schema.json.license \
        adapter/workflow adapter/upgrades adapter/results
    git diff --cached --check
    git commit --quiet -s -m "chore(adapter): checkpoint paused Ascend upgrade and cleanup evidence"
    git log -1 --oneline

    if ! git merge --no-ff --no-commit "$CANDIDATE_BRANCH"; then
        test -f "$(git rev-parse --git-path MERGE_HEAD)"
        mapfile -t conflicts < <(git diff --name-only --diff-filter=U)
        for path in "${conflicts[@]}"; do
            case "$path" in
                .gitattributes|.gitignore|adapter/upgrades/README.md|adapter/upgrades/ascend-1.6.2-scope-20261112.md) ;;
                *) printf 'Unexpected conflict; inspect before continuing: %s\n' "$path" >&2; exit 1 ;;
            esac
        done
        # Reviewed union: current ignore rules include upstream generated paths;
        # current docs preserve scope and add the user's explicit pause decision.
        for path in "${conflicts[@]}"; do
            git checkout --ours -- "$path"
            git add -- "$path"
        done
    fi
    test -z "$(git diff --name-only --diff-filter=U)"
    git diff --cached --check
    git diff --cached --exit-code "$CANDIDATE" -- \
        components container lib deploy Cargo.toml Cargo.lock pyproject.toml
    git commit --quiet -s -m "merge: preserve Dynamo 1.6.2 Ascend adaptation at paused checkpoint" \
        -m "Retain migration, comparison, backup and cleanup evidence. Remaining qualification and delivery gates stay pending; this is not a certified release."
    printf '\nMerge saved. Next: git push origin heyi\nThen: bash adapter/upgrades/checkpoint-heyi.sh cleanup\n'
    ;;
cleanup)
    MAIN_BEFORE=$(git rev-parse refs/heads/main)
    git merge-base --is-ancestor "$CANDIDATE" heyi
    git merge-base --is-ancestor "$SOURCE_BRANCH" heyi
    git diff --quiet
    git diff --cached --quiet
    # Require successful push before removing local branch names.
    git merge-base --is-ancestor heyi origin/heyi
    while IFS= read -r branch; do
        case "$branch" in
            heyi|main|"$SOURCE_BRANCH"|"$CANDIDATE_BRANCH") ;;
            *) printf 'Unexpected local branch; retained: %s\n' "$branch" >&2; exit 1 ;;
        esac
    done < <(git for-each-ref --format='%(refname:short)' refs/heads/)
    BACKUP="$(dirname "$ROOT")/dynamo-before-temp-branch-cleanup-$(date -u +%Y%m%dT%H%M%SZ).bundle"
    test ! -e "$BACKUP"
    git bundle create "$BACKUP" --all
    git bundle verify "$BACKUP" > "$BACKUP.verify.log" 2>&1
    sha256sum "$BACKUP" > "$BACKUP.sha256"
    git -C "$CANDIDATE_WT" switch --detach "$CANDIDATE"
    git -C "$SOURCE_WT" switch --detach "$SOURCE_BRANCH"
    git branch -d "$CANDIDATE_BRANCH" "$SOURCE_BRANCH"
    # Preserve main exactly; only the two named temporary branches are removed.
    test "$(git rev-parse refs/heads/main)" = "$MAIN_BEFORE"
    test "$(git for-each-ref --format='%(refname:short)' refs/heads/)" = $'heyi\nmain'
    printf '\nTemporary branches removed; heyi and main preserved. Git backup: %s\n' "$BACKUP"
    git worktree list
    ;;
*)
    printf 'Usage: bash adapter/upgrades/checkpoint-heyi.sh {merge|cleanup}\n' >&2
    exit 2
    ;;
esac
