# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Versioned catalog and durable campaign primitives; no service dependency."""

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import jsonschema
import yaml

from adapter.workflow.config import validate_environment

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "adapter"
RESULTS = ADAPTER / "results"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read(path):
    with Path(path).open() as stream:
        return yaml.safe_load(stream)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


@contextlib.contextmanager
def lock(path):
    """Kernel releases lock on crash; persisted running attempts still need reconciliation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"Resource already leased: {path.name}") from exc
        yield


def event(campaign, kind, **fields):
    with (Path(campaign) / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(dict(time=now(), event=kind, **fields)) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def catalog():
    result = {}
    for p in sorted((ADAPTER / "modules").glob("*/features/*/feature.yaml")):
        f = read(p)
        jsonschema.validate(f, read(ADAPTER / "contracts/schemas/feature.schema.json"))
        if f["id"] in result:
            raise ValueError(f"Duplicate feature {f['id']}")
        f["contract_path"] = str(p.relative_to(ROOT))
        f["contract_sha256"] = digest(p)
        result[f["id"]] = f
    for f in result.values():
        for group in f.get("dependencies_any_of", []):
            if not group or any(k not in result for k in group):
                raise ValueError("Invalid any-of dependency: " + f["id"])
    # Also validates unknown references and cycles.
    order(result, list(result))
    return result


def order(features, selected):
    active, done, output = set(), set(), []

    def visit(key):
        if key not in features:
            raise ValueError(f"Unknown feature: {key}")
        if key in active:
            raise ValueError(f"Dependency cycle: {key}")
        if key in done:
            return
        active.add(key)
        for dep in features[key]["dependencies"]:
            visit(dep)
        active.remove(key)
        done.add(key)
        output.append(key)

    for key in selected:
        visit(key)
    return output


def source_identity():
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    # Include actual tracked edits and new implementation files, not only HEAD.
    paths = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "adapter",
                "components",
                "lib",
                "container",
                "deploy",
                "Cargo.toml",
                "Cargo.lock",
                "rust-toolchain.toml",
                "pyproject.toml",
                "uv.lock",
                ".cargo",
                ".ai",
                "AGENTS.md",
                ".agents/skills",
                "scripts",
            ],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    files = {}
    for name in sorted(set(paths)):
        if (
            not name
            or "/results/" in name
            or name.endswith(".license")
            or "__pycache__" in name
            or name
            in {
                "adapter/modules.md",
                "adapter/support-matrix.md",
                "adapter/version-baselines.md",
                "adapter/workload.md",
                "adapter/project-statistics.md",
            }
        ):
            continue
        p = ROOT / name
        if p.is_file() and not p.is_symlink():
            files[name] = digest(p)
    return {"head": head, "files_sha256": fingerprint(files), "files": files}


def platform_stack(platform, profile_path):
    path = (ROOT / profile_path).resolve()
    if (
        path.parent != (ROOT / "container/platforms").resolve()
        or path.suffix != ".json"
    ):
        raise ValueError(
            "Stack lock must be a canonical container/platforms/*.json file"
        )
    stack = read(path)
    if stack.get("id") != platform or not stack.get("versions", {}).get("vllm"):
        raise ValueError("Platform ID and vLLM version must match the canonical stack")
    if not re.search(r"@sha256:[a-f0-9]{64}$", stack.get("base_image", "")):
        raise ValueError("Canonical stack base image must be digest-pinned")
    return path, stack


def init_campaign(destination, platform, chip, environment, selected=None, scope=None):
    from adapter.workflow.identity import signature
    from adapter.workflow.scope import resolve

    identifier(platform)
    identifier(chip)
    destination = Path(destination)
    if len(destination.parts) == 1:
        destination = RESULTS / platform / chip / identifier(destination.name)
    destination = destination.resolve()
    if destination.parent != (RESULTS / platform / chip).resolve():
        raise ValueError(
            "Campaign must be under adapter/results/<platform>/<chip>/<campaign>"
        )
    if destination.exists():
        raise ValueError("Campaign already exists; resume it or create a new identity")
    identifier(platform)
    identifier(chip)
    profile = read(ADAPTER / "platforms" / platform / "platform.yaml")
    stack_path, stack = platform_stack(platform, profile["stack_profile"])
    env = read(environment)
    try:
        jsonschema.validate(
            env, read(ADAPTER / "contracts/schemas/environment.schema.json")
        )
    except jsonschema.ValidationError as exc:
        raise ValueError("Invalid environment: " + exc.message) from exc
    validate_environment(env)
    features = catalog()
    if selected and set(selected) - set(features):
        raise ValueError("Unknown selected feature")
    declaration, planned, contracts = resolve(features, selected, scope)
    source = source_identity()
    frozen = {
        "schema_version": 1,
        "platform": platform,
        "chip": chip,
        "environment": env,
        "stack": stack,
        "stack_profile": profile["stack_profile"],
        "stack_sha256": digest(stack_path),
        "policy": read(ADAPTER / "contracts/policy.yaml"),
        "source": source,
        "features": contracts,
        "scope": declaration,
        "required": [
            k
            for k in planned
            if declaration["features"][k]["disposition"] == "required"
        ],
        "planned": planned,
        "feature_inputs": {
            k: signature(f, source, profile["stack_profile"], fingerprint)
            for k, f in contracts.items()
        },
    }
    frozen["identity"] = fingerprint(frozen)
    destination.mkdir(parents=True)
    atomic(destination / "lock.json", frozen)
    atomic(
        destination / "state.json",
        {
            "schema_version": 1,
            "identity": frozen["identity"],
            "created": now(),
            "features": {
                k: {
                    "implementation": "not_started",
                    "task": "queued",
                    "test": "not_run",
                    "qualification": "pending",
                    "attempts": [],
                }
                for k in planned
            },
        },
    )
    atomic(destination / "tasks.json", {"schema_version": 1, "tasks": []})
    event(destination, "initialized", identity=frozen["identity"])
    return destination


def load_campaign(path):
    path = Path(path).resolve()
    frozen, state = read(path / "lock.json"), read(path / "state.json")
    identity = frozen.pop("identity")
    if fingerprint(frozen) != identity or state["identity"] != identity:
        raise ValueError(
            "Frozen contract identity mismatch; create a new campaign for changed inputs"
        )
    frozen["identity"] = identity
    return path, frozen, state


def freshness(frozen):
    """Qualification remains attached to its original identity, never silently reused."""
    from adapter.workflow.identity import freshness_map

    if any(
        v["freshness"] != "valid"
        for v in freshness_map(frozen, source_identity(), fingerprint).values()
    ):
        return "stale"
    if any(
        not (ROOT / f["contract_path"]).is_file()
        or digest(ROOT / f["contract_path"]) != f["contract_sha256"]
        for f in frozen["features"].values()
    ):
        return "stale"
    return "valid"
