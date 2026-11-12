#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render final-image regression manifests, rejecting development overlays."""

import argparse
import copy
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
OVERLAYS = {"m09-source", "m09-deps"}


def release_manifest(obj, platform, namespace, runtime, tools, runtime_version):
    """Retain measured engine parameters, replacing only delivery bindings."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", runtime_version):
        raise ValueError("Supply the measured stable Dynamo runtime version")
    obj = json.loads(
        json.dumps(obj)
        .replace("dynamo-m08-1029", namespace)
        .replace("http://m09-metrics:", "http://m10-metrics:")
    )
    obj["metadata"]["name"] = (
        obj["metadata"]["name"].replace("m08-", "m10-").replace("m09-", "m10-")
    )
    obj["metadata"]["namespace"] = namespace
    obj["metadata"].setdefault("labels", {})["heyi.io/campaign"] = namespace
    for component in obj["spec"]["components"]:
        component["runtimeVersionOverride"] = runtime_version
        pod = component["podTemplate"]["spec"]
        pod["volumes"] = [
            v for v in pod.get("volumes", []) if v["name"] not in OVERLAYS
        ]
        for container in pod["containers"]:
            container["image"] = tools if component["type"] == "planner" else runtime
            container["imagePullPolicy"] = "Never"
            if platform == "ascend" and component["type"] == "planner":
                container["command"] = [
                    word.replace("/opt/dynamo/venv/bin/", "/opt/dynamo/tools-venv/bin/")
                    for word in container.get("command", [])
                ]
            container["volumeMounts"] = [
                m
                for m in container.get("volumeMounts", [])
                if m["name"] not in OVERLAYS
            ]
            container["env"] = [
                e for e in container.get("env", []) if e["name"] != "PYTHONPATH"
            ]
    encoded = json.dumps(obj)
    for forbidden in (
        "/m09-source",
        "/m09-deps",
        "/workspace/dynamo",
        "1.5.1-m02",
        "1.5.1-m08",
    ):
        if forbidden in encoded:
            raise ValueError(
                f"Development or stale image reference remains: {forbidden}"
            )
    return obj


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["cuda", "ascend"], required=True)
    parser.add_argument("--namespace", default="dynamo-m10")
    parser.add_argument("--runtime-image", required=True)
    parser.add_argument(
        "--runtime-version",
        required=True,
        help="Installed Dynamo version of both delivery images",
    )
    parser.add_argument("--tools-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--workdir",
        required=True,
        help="Host directory containing harness and evidence directories",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifests = {}
    for name, source in {
        "aggregate": "cluster",
        "pd": "cluster",
        "pd-cross": "advanced",
        "grove": "advanced",
        "grove-kai": "advanced",
    }.items():
        manifests[name] = json.loads(
            (
                ROOT
                / f"adapter/modules/m08-kubernetes/common/{source}/manifests/{args.platform}/{name}.json"
            ).read_text()
        )
    for name, script, extra in [
        ("planner-small", "render.py", []),
        ("planner-sla", "render.py", ["--sla"]),
        ("planner-qwen32", "render_large.py", ["--model", "qwen32"]),
        ("planner-deepseek", "render_large.py", ["--model", "deepseek"]),
    ]:
        command = [
            sys.executable,
            str(
                ROOT / "adapter/modules/m09-telemetry-planner/common/rendering" / script
            ),
            "--platform",
            args.platform,
            "--workdir",
            "/m10-unused",
            *extra,
        ]
        manifests[name] = json.loads(subprocess.check_output(command))
    with tempfile.TemporaryDirectory(prefix="m10-rdma-") as temporary:
        output = Path(temporary) / "rdma.json"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "adapter/modules/m08-kubernetes/common/rdma/render.py"),
                "--platform",
                args.platform,
                "--output",
                str(output),
            ],
            check=True,
        )
        manifests["pd-rdma"] = json.loads(output.read_text())
    for name, obj in manifests.items():
        result = release_manifest(
            obj,
            args.platform,
            args.namespace,
            args.runtime_image,
            args.tools_image,
            args.runtime_version,
        )
        (args.output / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    aggregate = release_manifest(
        manifests["aggregate"],
        args.platform,
        args.namespace,
        args.runtime_image,
        args.tools_image,
        args.runtime_version,
    )
    pod = copy.deepcopy(
        next(c for c in aggregate["spec"]["components"] if c["type"] == "worker")[
            "podTemplate"
        ]["spec"]
    )
    container = pod["containers"][0]
    resource = "nvidia.com/gpu" if args.platform == "cuda" else "huawei.com/Ascend910"
    container["resources"] = {
        "requests": {resource: 2, "cpu": "8", "memory": "32Gi"},
        "limits": {resource: 2, "cpu": "16", "memory": "64Gi"},
    }
    container["args"] = [
        "bash",
        "/harness/run-hardware.sh",
        args.platform,
        "/evidence/hardware",
        "/models/Qwen3-0.6B",
    ]
    for name, host, mount in [
        ("harness", args.workdir + "/harness", "/harness"),
        ("evidence", args.workdir + "/evidence", "/evidence"),
    ]:
        pod["volumes"].append(
            {"name": name, "hostPath": {"path": host, "type": "Directory"}}
        )
        container["volumeMounts"].append(
            {"name": name, "mountPath": mount, "readOnly": name == "harness"}
        )
    pod["volumes"].append(
        {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "16Gi"}}
    )
    container["volumeMounts"].append({"name": "shm", "mountPath": "/dev/shm"})
    pod.update(
        restartPolicy="Never",
        automountServiceAccountToken=False,
        terminationGracePeriodSeconds=180,
    )
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "m10-hardware", "namespace": args.namespace},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 7200,
            "template": {
                "metadata": {"labels": {"heyi.io/campaign": args.namespace}},
                "spec": pod,
            },
        },
    }
    (args.output / "hardware-job.json").write_text(json.dumps(job, indent=2) + "\n")
    print(
        f"Rendered {len(manifests)} final-image DGD manifests and one hardware Job in {args.output}"
    )


if __name__ == "__main__":
    main()
