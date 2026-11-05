# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Feature input fingerprints and transitive invalidation, independent of report churn."""

from pathlib import PurePosixPath

COMMON = (
    "adapter/workflow",
    "adapter/contracts",
    "Cargo.toml",
    "Cargo.lock",
    "pyproject.toml",
    "uv.lock",
    "rust-toolchain.toml",
    ".cargo",
    ".ai",
    "AGENTS.md",
    ".agents/skills/adapter-",
)


def relevant(name, roots):
    return any(
        name == root
        or name.startswith(root.rstrip("/") + "/")
        or (root.endswith("-") and name.startswith(root))
        for root in roots
    )


def signature(feature, source, stack_profile, fingerprint):
    if "files" not in source:
        return fingerprint(
            source
        )  # Older imported/fixture identities are conservatively invalidated.
    roots = (
        list(COMMON) + feature["implementation_paths"] + feature.get("entrypoints", [])
    )
    roots += [feature["contract_path"], stack_profile]
    roots += [str(PurePosixPath(feature["contract_path"]).parents[2] / "common")]
    return fingerprint({p: h for p, h in source["files"].items() if relevant(p, roots)})


def freshness_map(frozen, current, fingerprint):
    direct, changed = {}, set()
    for key, feature in frozen["features"].items():
        before = frozen.get("feature_inputs", {}).get(key)
        if before is None:
            before = signature(
                feature, frozen["source"], frozen["stack_profile"], fingerprint
            )
        direct[key] = before != signature(
            feature, current, frozen["stack_profile"], fingerprint
        )
        if direct[key]:
            changed.add(key)
    while True:
        extra = {
            k
            for k, f in frozen["features"].items()
            if any(d in changed for d in f["dependencies"])
        }
        if extra <= changed:
            break
        changed |= extra
    return {
        k: {
            "freshness": "stale" if k in changed else "valid",
            "direct": direct[k],
            "affected_dependencies": [d for d in f["dependencies"] if d in changed],
        }
        for k, f in frozen["features"].items()
    }
