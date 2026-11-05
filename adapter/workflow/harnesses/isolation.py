# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Isolated implementation snapshots with checked, scope-limited patch application."""

import shutil

from adapter.workflow.core import (
    ROOT,
    atomic,
    digest,
    fingerprint,
    lock,
    source_identity,
)

PROTECTED = (
    "adapter/contracts",
    "adapter/workflow",
    "adapter/platforms",
    "container/platforms",
    ".agents",
    "AGENTS.md",
    "CLAUDE.md",
)


def within(name, roots):
    return any(name == p or name.startswith(p.rstrip("/") + "/") for p in roots)


def allowed(name, paths):
    return (
        within(name, paths)
        and not within(name, PROTECTED)
        and "/features/" not in name
        and not name.endswith("/AGENTS.md")
        and not name.endswith("/CLAUDE.md")
    )


def snapshot(target):
    inventory = source_identity()["files"]
    root = target / "workspace"
    root.mkdir()
    for name in inventory:
        file = ROOT / name
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, dest)
    atomic(target / "workspace-base.json", inventory)
    return root, inventory


def apply(root, inventory, paths, target):
    # Exclude only interpreter output, never unreviewed source or symlink changes.
    files = {
        str(p.relative_to(root)): p
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("Agent produced a symlink; no changes applied")
    changed = [
        name
        for name in sorted(set(files) | set(inventory))
        if (digest(files[name]) if name in files else None) != inventory.get(name)
    ]
    forbidden = [n for n in changed if not allowed(n, paths)]
    if forbidden:
        raise ValueError(
            "Agent changed protected/out-of-scope files: " + str(forbidden)
        )
    with lock(ROOT / "adapter/results/.implementation.lock"):
        for name in changed:
            destination = ROOT / name
            if (
                digest(destination) if destination.is_file() else None
            ) != inventory.get(name):
                raise ValueError("Concurrent source edit; no changes applied: " + name)
        backup = target / "patch-base"
        for name in changed:
            destination = ROOT / name
            if destination.exists():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            if name in files:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(files[name], destination)
            else:
                destination.unlink()
    receipt = {
        "changed_files": changed,
        "base_sha256": fingerprint(inventory),
        "after": {
            n: digest(ROOT / n) if (ROOT / n).exists() else None for n in changed
        },
    }
    atomic(target / "patch-receipt.json", receipt)
    return receipt
