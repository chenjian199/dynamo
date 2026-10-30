#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render a repeat of certified M08 thorough measurements with M09 source/identity."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--workdir", required=True)
a = p.parse_args()
d = json.loads(
    (ROOT / f"adapter/m08/advanced/manifests/{a.platform}/dgdr.json").read_text()
)
d["metadata"]["name"] = "m09-profile"
d["metadata"]["labels"] = {"heyi.io/campaign": "m09-1029"}
d["spec"]["autoApply"] = False
spec = d["spec"]["overrides"]["profilingJob"]["template"]["spec"]
for volume in spec["volumes"]:
    if volume["name"] == "profiling-output":
        volume["hostPath"]["path"] = a.workdir + "/profile-output"
spec["volumes"] += [
    {
        "name": "m09-source",
        "hostPath": {"path": a.workdir + "/profiler-source", "type": "Directory"},
    },
    {
        "name": "m09-identity",
        "hostPath": {"path": a.workdir + "/profile-identity.json", "type": "File"},
    },
]
c = next(c for c in spec["containers"] if c["name"] == "profiler")
c["env"] += [
    {
        "name": "PYTHONPATH",
        "value": "/m09-source/components/src:/opt/dynamo/profiler-support",
    },
    {"name": "DYN_PROFILE_IDENTITY", "value": "/m09-profile-identity.json"},
]
c["volumeMounts"] += [
    {"name": "m09-source", "mountPath": "/m09-source", "readOnly": True},
    {
        "name": "m09-identity",
        "mountPath": "/m09-profile-identity.json",
        "readOnly": True,
    },
]
if a.platform == "ascend":
    # The existing tool image contains the K3s multicall kubectl. Its default
    # extraction directory is not writable by the Operator's non-root UID.
    copier = next(c for c in spec["containers"] if c["name"] == "output-copier")
    copier.setdefault("env", []).append(
        {"name": "K3S_DATA_DIR", "value": "/tmp/m09-k3s"}
    )
# All measured workers also run the fixed M09 candidate source. Existing AIPerf
# and deployment-client overlays from the certified tool image remain in place.
for component in d["spec"]["overrides"]["dgd"]["spec"]["components"]:
    pod = component["podTemplate"]["spec"]
    pod.setdefault("volumes", []).append(
        {
            "name": "m09-source",
            "hostPath": {"path": a.workdir + "/profiler-source", "type": "Directory"},
        }
    )
    c = pod["containers"][0]
    c.setdefault("volumeMounts", []).append(
        {"name": "m09-source", "mountPath": "/m09-source", "readOnly": True}
    )
    c.setdefault("env", []).append(
        {"name": "PYTHONPATH", "value": "/m09-source/components/src"}
    )
print(json.dumps(d, indent=2))
