# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Explicit argv execution; SSH/container/Kubernetes never select implicit contexts."""

import os
import signal
import subprocess
from pathlib import Path

from adapter.workflow.preflight import ssh_command


def substitute(spec, environment, repo, attempt, identity):
    """Resolve paths in the executor's filesystem; never assume a shared mount."""
    spec = dict(spec)
    kind = spec.get("executor", "local")
    target_repo, target_attempt = str(repo), str(attempt)
    try:
        result_relative = Path("adapter/results") / attempt.relative_to(
            Path(repo) / "adapter/results"
        )
    except ValueError:
        result_relative = (
            Path("adapter/results/fixture/fixture")
            / identity
            / "features"
            / attempt.parent.name
            / attempt.name
        )
    if kind in {"ssh", "container"}:
        node = next((n for n in environment["nodes"] if n["id"] == spec["node"]), None)
        if node is None:
            raise ValueError("Executor node is not declared in the environment")
        target_repo = node["workspace"]
        if kind == "container":
            target_repo = node.get("container_workspace")
            if not target_repo:
                raise ValueError("Declare container_workspace for container task paths")
        target_attempt = str(Path(target_repo) / result_relative)
        spec.setdefault("cwd", target_repo)
    elif kind == "kubernetes" and environment["kubernetes"].get("host"):
        controller = next(
            (
                n
                for n in environment["nodes"]
                if n.get("ssh") == environment["kubernetes"]["host"]
            ),
            None,
        )
        if not controller:
            raise ValueError(
                "Remote Kubernetes controller must have a declared workspace node"
            )
        target_repo = controller["workspace"]
        target_attempt = str(Path(target_repo) / result_relative)

    def expand(value):
        return (
            value.replace("{repo}", target_repo)
            .replace("{attempt}", target_attempt)
            .replace("{campaign}", str(Path(target_attempt).parents[2]))
            .replace("{coordinator_attempt}", str(attempt))
            .replace("{identity}", identity)
        )

    spec["argv"] = [expand(v) for v in spec["argv"]]
    if "cwd" in spec:
        spec["cwd"] = expand(spec["cwd"])
    return spec


def command(spec, environment):
    argv = spec.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(a, str) for a in argv)
    ):
        raise ValueError(
            "Commands require a nonempty string argv, never an implicit shell"
        )
    kind = spec.get("executor", "local")
    if kind == "local":
        return argv
    nodes = {n["id"]: n for n in environment["nodes"]}
    if kind == "ssh":
        if spec.get("cwd"):
            argv = [
                "sh",
                "-c",
                'cd -- "$1" && shift && exec "$@"',
                "adapter-exec",
                spec["cwd"],
                *argv,
            ]
        return ssh_command(
            nodes[spec["node"]].get("ssh"), argv, environment.get("ssh_config")
        )
    if kind == "container":
        # Use an existing named development/test container, never pick the first running container.
        cwd_args = ["-w", spec["cwd"]] if spec.get("cwd") else []
        return ssh_command(
            nodes[spec["node"]].get("ssh"),
            ["docker", "exec", *cwd_args, spec["container"], *argv],
            environment.get("ssh_config"),
        )
    if kind == "kubernetes":
        k = environment["kubernetes"]
        if spec.get("cwd"):
            raise ValueError(
                "Kubernetes executor does not accept cwd; use explicit manifest paths"
            )
        return ssh_command(
            k.get("host"),
            ["kubectl", "--context", k["context"], "-n", k["namespace"], *argv],
            environment.get("ssh_config"),
        )
    raise ValueError(f"Unknown executor: {kind}")


def execute(spec, environment, cwd, log, timeout, on_start, stdin_text=None):
    argv = command(spec, environment)
    with Path(log).open("w") as stream:
        proc = subprocess.Popen(
            argv,
            cwd=spec.get("cwd", cwd)
            if spec.get("executor", "local") == "local"
            else cwd,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            stdin=subprocess.PIPE if stdin_text is not None else None,
        )
        try:
            on_start(proc.pid)
            if stdin_text is not None:
                proc.communicate(input=stdin_text.encode(), timeout=timeout)
                return proc.returncode
            return proc.wait(timeout=timeout)
        except BaseException:
            # Even a persistence/callback failure must not leak a child process.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            raise
