#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Server-side admission checks; negative manifests are never persisted."""

import argparse
import copy
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--manifest", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
base = json.loads(a.manifest.read_text())


def server(obj):
    result = subprocess.run(
        ["kubectl", "apply", "--dry-run=server", "-f", "-"],
        input=json.dumps(obj).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
    )
    return result.returncode, result.stdout.decode()


def platform(component, name):
    resource = {"cuda": "nvidia.com/gpu", "ascend": "huawei.com/Ascend910"}[name]
    c = component["podTemplate"]["spec"]["containers"][0]
    for e in c["env"]:
        if e["name"] == "DYN_PLATFORM":
            e["value"] = name
    for kind in ["requests", "limits"]:
        for key in list(c["resources"][kind]):
            if key in ["nvidia.com/gpu", "huawei.com/Ascend910"]:
                del c["resources"][kind][key]
        c["resources"][kind][resource] = 1
    return c


rc, log = server(base)
assert rc == 0, log
records = []
for case, expected in [
    ("conflict", "conflicts"),
    ("mixed", "platform"),
    ("vnpu", "unsupported allocation unit"),
    ("soft-share", "soft-sharing"),
    ("gms", "GMS/failover is not supported"),
    ("dra", "DRA is not supported"),
]:
    obj = copy.deepcopy(base)
    obj["metadata"]["name"] = "reject-" + case
    worker = obj["spec"]["components"][1]
    c = platform(worker, "ascend")
    if case == "conflict":
        next(e for e in c["env"] if e["name"] == "DYN_PLATFORM")["value"] = "cuda"
    elif case == "mixed":
        other = copy.deepcopy(worker)
        other["name"] = "other-worker"
        platform(other, "cuda")
        obj["spec"]["components"].append(other)
    elif case == "vnpu":
        for kind in ["requests", "limits"]:
            del c["resources"][kind]["huawei.com/Ascend910"]
            c["resources"][kind]["huawei.com/Ascend910c-4c"] = 1
    elif case == "soft-share":
        worker["podTemplate"]["metadata"] = {
            "annotations": {"huawei.com/scheduler.softShareDev.Ascend910": "1"}
        }
    elif case == "gms":
        worker["experimental"] = {"gpuMemoryService": {}}
    elif case == "dra":
        worker["podTemplate"]["spec"]["resourceClaims"] = [
            {"name": "npu", "resourceClaimName": "not-created"}
        ]
        c["resources"]["claims"] = [{"name": "npu"}]
    rc, log = server(obj)
    records.append(
        {
            "case": case,
            "returncode": rc,
            "expected": expected,
            "passed": rc != 0 and expected.lower() in log.lower(),
            "output": log,
        }
    )
a.output.write_text(json.dumps(records, indent=2))
assert all(r["passed"] for r in records), records
print("Admission checks passed:", len(records))
