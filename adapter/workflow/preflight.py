# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only, per-node environment inventory. Missing prerequisites block only dependents."""

import json
import re
import shlex
import subprocess
from pathlib import Path

from adapter.workflow.bindings import case_environment
from adapter.workflow.config import valid_hardware_uuid
from adapter.workflow.core import atomic, event, fingerprint, load_campaign, lock, now


def ssh_command(host, argv, config=None):
    if host:
        if host.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_@.:-]+", host):
            raise ValueError("Invalid SSH alias")
        return [
            "ssh",
            *(["-F", config] if config else []),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            host,
            shlex.join(argv),
        ]
    return argv


def execute(argv, host=None, timeout=25, config=None):
    try:
        p = subprocess.run(
            ssh_command(host, argv, config),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "passed": p.returncode == 0,
            "returncode": p.returncode,
            # Kubernetes node JSON may exceed a terminal log-sized buffer.
            "stdout": p.stdout,
            "stderr": p.stderr[-4096:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "error": str(exc)}


def inspect_environment(env, requirements, runner=execute):
    checks = []

    def add(node, requirement, argv=None, host=None, reason=None, timeout=None):
        outcome = (
            runner(
                argv,
                host=host,
                **({"timeout": timeout} if timeout else {}),
                **({"config": env["ssh_config"]} if env.get("ssh_config") else {}),
            )
            if argv
            else {"passed": False, "error": reason}
        )
        checks.append(dict(node=node, requirement=requirement, command=argv, **outcome))
        return outcome

    machine_ids, hardware_ids = {}, {}
    for n in env["nodes"]:
        host = n.get("ssh")
        ident = n["id"]
        auth = add(ident, "ssh", ["true"], host)
        if not auth["passed"]:
            checks[-1]["remediation"] = (
                "Configure a known host and ssh-copy-id/key authorization interactively, then verify ssh -o BatchMode=yes ALIAS true. Never store private keys or passwords in this manifest."
            )
            for requirement in set(requirements) - {"ssh"}:
                add(ident, requirement, reason="SSH unavailable; not probed")
            continue
        add(ident, "workspace", ["test", "-w", n["workspace"]], host)
        add(ident, "inventory", ["uname", "-a"], host)
        machine = add(ident, "inventory", ["cat", "/etc/machine-id"], host)
        machine_id = machine.get("stdout", "").strip()
        if machine.get("passed") and re.fullmatch(r"[0-9a-f]{32}", machine_id):
            machine_ids[ident] = machine_id
        if n.get("machine_id") and machine_id != n["machine_id"]:
            add(
                ident,
                "ssh",
                reason="SSH target identity differs from declared machine_id",
            )
        if n.get("host_identity"):
            # Cloned OS images can share machine-id. An explicit hardware UUID
            # binding must be observed over SSH; a hostname/alias is insufficient.
            observed = add(
                ident, "inventory", ["cat", "/sys/class/dmi/id/product_uuid"], host
            )
            hardware_uuid = observed.get("stdout", "").strip().lower()
            binding = n["host_identity"]
            if (
                binding.get("kind") == "dmi_product_uuid"
                and observed.get("passed")
                and valid_hardware_uuid(hardware_uuid)
                and hardware_uuid == binding.get("value")
            ):
                hardware_ids[ident] = hardware_uuid
            else:
                add(
                    ident,
                    "ssh",
                    reason="Observed hardware UUID differs from pinned host_identity",
                )
        if "device" in requirements:
            add(
                ident,
                "device",
                n.get("device_probe"),
                host,
                reason="device_probe is required for this platform",
            )
            for device in n.get("device_paths", []):
                add(ident, "device", ["test", "-r", device], host)
        runtime = n.get("runtime")
        if "runtime" in requirements:
            commands = {
                "docker": ["docker", "info", "--format", "{{.ServerVersion}}"],
                "containerd": ["crictl", "info"],
                "k3s": ["k3s", "crictl", "info"],
            }
            add(
                ident,
                "runtime",
                commands.get(runtime),
                host,
                reason="Declare docker, containerd or k3s",
            )
        if "images" in requirements:
            if not n.get("images"):
                add(
                    ident,
                    "images",
                    reason="List required immutable image digests on every execution node",
                )
            for image in n.get("images", []):
                if "@sha256:" not in image or not re.search(
                    r"@sha256:[a-f0-9]{64}$", image
                ):
                    add(
                        ident,
                        "images",
                        reason="Images must be digest-locked, not mutable tags",
                    )
                    continue
                command = {
                    "docker": ["docker", "image", "inspect", image],
                    "containerd": ["crictl", "inspecti", image],
                    "k3s": ["k3s", "crictl", "inspecti", image],
                }.get(runtime)
                add(ident, "images", command, host, reason="Unknown runtime")
        if "models" in requirements:
            if not n.get("models"):
                add(
                    ident,
                    "models",
                    reason="List readable model roots; no implicit downloads",
                )
            for model in n.get("models", []):
                if not isinstance(model, dict) or not all(
                    model.get(k)
                    for k in ("id", "path", "revision", "manifest", "manifest_sha256")
                ):
                    add(
                        ident,
                        "models",
                        reason="Pin model id/revision and manifest SHA256; a directory alone is not model identity",
                    )
                    continue
                script = Path(__file__).with_name("assets.py").read_text()
                add(
                    ident,
                    "models",
                    ["python3", "-c", script, json.dumps(model)],
                    host,
                    timeout=env.get("asset_probe_timeout_seconds", 25),
                )
        if "network" in requirements:
            add(
                ident,
                "network",
                n.get("network_probe"),
                host,
                reason="Declare network inventory command; transport correctness requires its own feature test",
            )
    if "multinode" in requirements:
        use_hardware = any(n.get("host_identity") for n in env["nodes"])
        identities = hardware_ids if use_hardware else machine_ids
        checks.append(
            {
                "node": "environment",
                "requirement": "multinode",
                "passed": len(identities) == len(env["nodes"])
                and len(set(identities.values())) == len(env["nodes"]) >= 2,
                "machine_ids": machine_ids,
                "hardware_ids": hardware_ids,
                "identity_kind": "dmi_product_uuid" if use_hardware else "machine_id",
                "reason": "Require distinct observed identities of one kind on all nodes, not two SSH aliases",
            }
        )
    if "kubernetes" in requirements:
        k = env.get("kubernetes", {})
        if not all(k.get(x) for x in ["context", "namespace", "resource_key", "nodes"]):
            add(
                "cluster",
                "kubernetes",
                reason="Kubernetes context, namespace, resource_key and node names required",
            )
        else:
            prefix = ["kubectl", "--context", k["context"]]
            data = add(
                "cluster",
                "kubernetes",
                prefix + ["get", "nodes", "-o", "json"],
                k.get("host"),
            )
            if data["passed"]:
                try:
                    nodes = {
                        n["metadata"]["name"]: n
                        for n in json.loads(data["stdout"])["items"]
                    }
                    for name in k["nodes"]:
                        n = nodes.get(name, {})
                        ready = any(
                            c["type"] == "Ready" and c["status"] == "True"
                            for c in n.get("status", {}).get("conditions", [])
                        )
                        capacity = int(
                            n.get("status", {})
                            .get("allocatable", {})
                            .get(k["resource_key"], 0)
                        )
                        checks.append(
                            {
                                "node": name,
                                "requirement": "kubernetes",
                                "passed": ready
                                and capacity >= k.get("minimum_devices_per_node", 1),
                                "ready": ready,
                                "allocatable": capacity,
                            }
                        )
                except (KeyError, TypeError, ValueError) as exc:
                    add(
                        "cluster", "kubernetes", reason=f"Invalid node inventory: {exc}"
                    )
            for resource in ["pods", "jobs.batch", "configmaps", "services"]:
                add(
                    "cluster",
                    "kubernetes",
                    prefix
                    + ["auth", "can-i", "create", resource, "-n", k["namespace"]],
                    k.get("host"),
                )
            for api in k.get("required_apis", []):
                # /apis/GROUP/VERSION is read-only, and works for both CRDs and built-in APIs.
                add(
                    "cluster",
                    "kubernetes",
                    prefix + ["get", "--raw", "/apis/" + api],
                    k.get("host"),
                )
            for resource in k.get("required_resources", []):
                add(
                    "cluster",
                    "kubernetes",
                    prefix + ["get", resource, "-n", k["namespace"], "-o", "name"],
                    k.get("host"),
                )
    readiness = {
        r: bool([c for c in checks if c["requirement"] == r])
        and all(c["passed"] for c in checks if c["requirement"] == r)
        for r in requirements
    }
    return {
        "schema_version": 1,
        "time": now(),
        "environment_sha256": fingerprint(env),
        "readiness": readiness,
        "checks": checks,
        "machine_ids": machine_ids,
        "passed": all(readiness.values()),
    }


def doctor(campaign):
    path, _, _ = load_campaign(campaign)
    with lock(path / ".lock"):
        path, frozen, _ = load_campaign(path)
        report = inspect_environment(frozen["environment"], ["ssh", "workspace"])
        report["cases"] = {}
        cache = {}
        for feature in frozen["features"].values():
            for case in feature["cases"]:
                try:
                    env, binding = case_environment(
                        frozen["environment"], feature, case
                    )
                    cache_key = fingerprint([env, feature["requirements"]])
                    if cache_key not in cache:
                        cache[cache_key] = inspect_environment(
                            env, feature["requirements"]
                        )
                    case_report = dict(cache[cache_key])
                    case_report["binding_sha256"] = fingerprint(binding)
                    case_report["runtime_identity"] = probe_runtime(
                        env, binding, frozen["stack"]
                    )
                    if case_report["runtime_identity"]["status"] == "mismatch":
                        case_report["passed"] = False
                    report["cases"][case["id"]] = case_report
                except (ValueError, KeyError, OSError, TypeError) as exc:
                    report["cases"][case["id"]] = {"passed": False, "error": str(exc)}
        report["passed"] = all(
            report["cases"][c["id"]]["passed"]
            for f in frozen["features"].values()
            for c in f["cases"]
            if f.get("case_scope", {}).get(c["id"], {}).get("disposition") != "optional"
        )
        report["identity"] = frozen["identity"]
        atomic(path / "preflight.json", report)
        event(path, "preflight", passed=report["passed"])
    return report


def probe_runtime(env, binding, stack, runner=execute):
    """Check existing targets now; newly created targets must be observed before qualification."""
    records = []
    script = Path(__file__).with_name("runtime_probe.py").read_text()
    for node in env["nodes"]:
        container = binding.get("containers", {}).get(node["id"])
        pod = binding.get("pods", {}).get(node["id"])
        if not container and not pod:
            continue
        args = [
            stack.get("python", "python3"),
            "-c",
            script,
            json.dumps(stack["versions"]),
        ]
        host, config = node.get("ssh"), env.get("ssh_config")
        if container:
            image = runner(
                ["docker", "inspect", "--format", "{{.Image}}", container],
                host=host,
                config=config,
            )
            expected = runner(
                ["docker", "image", "inspect", "--format", "{{.Id}}", binding["image"]],
                host=host,
                config=config,
            )
            identity_ok = (
                image.get("passed")
                and expected.get("passed")
                and image.get("stdout", "").strip()
                == expected.get("stdout", "").strip()
            )
            result = runner(
                ["docker", "exec", container, *args], host=host, config=config
            )
        else:
            k = env["kubernetes"]
            prefix = ["kubectl", "--context", k["context"], "-n", k["namespace"]]
            image = runner(
                prefix + ["get", "pod", pod, "-o", "json"],
                host=k.get("host"),
                config=config,
            )
            try:
                actual = json.loads(image.get("stdout", "{}"))
                statuses = actual["status"]["containerStatuses"]
                selected = binding.get("pod_containers", {}).get(node["id"])
                if selected is None:
                    if len(statuses) != 1:
                        raise ValueError(
                            "Multi-container Pod requires pod_containers binding"
                        )
                    selected = statuses[0]["name"]
                observed = next(v for v in statuses if v["name"] == selected)
                observed_digest = observed["imageID"].split("://")[-1].split("@")[-1]
                identity_ok = observed_digest == binding["image"].split("@")[-1]
            except (ValueError, KeyError, TypeError, StopIteration):
                identity_ok = False
                selected = None
            result = (
                runner(
                    prefix
                    + [
                        "exec",
                        pod,
                        *(["-c", selected] if selected else []),
                        "--",
                        *args,
                    ],
                    host=k.get("host"),
                    config=config,
                )
                if selected
                else {"passed": False, "error": "Pod runtime identity unresolved"}
            )
        records.append(
            {
                "node": node["id"],
                "identity_ok": bool(identity_ok),
                "image": image,
                "versions": result,
                "passed": bool(identity_ok and result.get("passed")),
            }
        )
    return {
        "status": ("matched" if all(r["passed"] for r in records) else "mismatch")
        if records
        else "observe_after_creation",
        "records": records,
    }
