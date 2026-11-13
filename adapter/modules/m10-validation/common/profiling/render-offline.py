#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bind the bounded M09 offline sweep to final M10 images and fresh identity."""

import sys
from pathlib import Path

# Resolve shared helpers from their functional groups for direct script execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rendering"))

import argparse
import json
import re
import subprocess
import tempfile

from render import ROOT, release_manifest

OVERLAYS = {
    "m09-source",
    "m09-deps",
    "profiler-tokenizer",
    "deployment-client",
    "profiler-dgd-platform",
    "profiler-dgd-materialization",
    "config-modifier",
    "preflight-source",
}


def clean(value, namespace, runtime, tools, runtime_version):
    if isinstance(value, list):
        return [clean(v, namespace, runtime, tools, runtime_version) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"volumes", "volumeMounts"}:
            item = [v for v in item if v["name"] not in OVERLAYS]
        if key == "env":
            item = [v for v in item if v["name"] != "PYTHONPATH"]
        if key == "image":
            item = runtime if "-runtime:" in item else tools
        if key == "runtimeVersionOverride":
            item = runtime_version
        if key == "namespace":
            item = namespace
        result[key] = clean(item, namespace, runtime, tools, runtime_version)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
    p.add_argument("--model", choices=["small", "qwen32", "deepseek"], default="small")
    p.add_argument("--runtime-image", required=True)
    p.add_argument("--runtime-version", required=True)
    p.add_argument("--runtime-id", required=True)
    p.add_argument("--tools-image", required=True)
    p.add_argument("--driver", required=True, help="Current measured driver identity")
    p.add_argument("--namespace", default="dynamo-m10")
    p.add_argument("--workdir", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--profile-identity", type=Path, required=True)
    p.add_argument("--large-provenance", type=Path)
    a = p.parse_args()
    if a.model != "small" and not a.large_provenance:
        p.error("--large-provenance is required for a large model")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", a.runtime_id):
        p.error("runtime-id must be the measured image ID")
    with tempfile.TemporaryDirectory(prefix="m10-offline-") as temporary:
        small = Path(temporary) / "small"
        subprocess.run(
            [
                sys.executable,
                str(
                    ROOT
                    / "adapter/modules/m09-telemetry-planner/common/rendering/render_offline.py"
                ),
                "--platform",
                a.platform,
                "--workdir",
                a.workdir,
                "--output",
                str(small),
                "--profile-identity",
                str(a.profile_identity),
            ],
            check=True,
        )
        source = small
        if a.model != "small":
            source = Path(temporary) / a.model
            subprocess.run(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "adapter/modules/m09-telemetry-planner/common/rendering/render_offline_large.py"
                    ),
                    "--platform",
                    a.platform,
                    "--model",
                    a.model,
                    "--provenance",
                    str(a.large_provenance),
                    "--small-input",
                    str(small / "input.json"),
                    "--small-tools",
                    str(small / "tools.json"),
                    "--workdir",
                    a.workdir,
                    "--output",
                    str(source),
                ],
                check=True,
            )
        data = clean(
            json.loads((source / "input.json").read_text()),
            a.namespace,
            a.runtime_image,
            a.tools_image,
            a.runtime_version,
        )
        data["dgd"] = release_manifest(
            data["dgd"],
            a.platform,
            a.namespace,
            a.runtime_image,
            a.tools_image,
            a.runtime_version,
        )
        # The measured DGD is passed directly to interpolation. Historical DGDR
        # profiling Job/source overrides do not participate in this test.
        data["dgdr"]["overrides"] = {"dgd": data["dgd"]}
        data["dgdr"]["image"] = a.tools_image
        data["identity"].update(runtime_image=a.runtime_id, driver=a.driver)
        # The worker-argument identity inherited from the aggregate template
        # does not include the PD transport added to the two role components.
        transports = {}
        for component in data["dgd"]["spec"]["components"]:
            if component["type"] not in ("prefill", "decode"):
                continue
            arguments = component["podTemplate"]["spec"]["containers"][0]["args"]
            if "--kv-transfer-config" in arguments:
                transports[component["type"]] = json.loads(
                    arguments[arguments.index("--kv-transfer-config") + 1]
                )
        data["identity"]["kv_config"]["pd_transports"] = transports
        data["dgdr"]["features"]["planner"]["profile_identity"] = data["identity"]
        tools = clean(
            json.loads((source / "tools.json").read_text()),
            a.namespace,
            a.runtime_image,
            a.tools_image,
            a.runtime_version,
        )
        tools["metadata"]["labels"] = {"heyi.io/campaign": a.namespace}
        tools["spec"]["volumes"].append(
            {"name": "capacity", "configMap": {"name": "m10-capacity"}}
        )
        tools["spec"]["containers"][0].setdefault("volumeMounts", []).append(
            {
                "name": "capacity",
                "mountPath": "/opt/dynamo/platform-capacity",
                "readOnly": True,
            }
        )
        rbac = json.loads((small / "rbac.json").read_text())
        a.output.mkdir(parents=True, exist_ok=True)
        for name, value in [("input", data), ("tools", tools), ("rbac", rbac)]:
            encoded = (
                json.dumps(value, indent=2)
                .replace("dynamo-m08-1029", a.namespace)
                .replace("m09-offline", "m10-offline")
            )
            if any(
                s in encoded
                for s in [
                    "/m09-source",
                    "/m09-deps",
                    "site-packages/",
                    "1.5.1-m02",
                    "1.5.1-m08",
                ]
            ):
                raise ValueError(f"Stale implementation overlay in {name}")
            (a.output / f"{name}.json").write_text(encoded + "\n")
    print(a.output)


if __name__ == "__main__":
    main()
