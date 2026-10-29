#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only cluster prerequisites for a manifest produced by render.py."""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    dgd = json.loads(args.manifest.read_text())
    namespace = dgd["metadata"]["namespace"]

    def get(kind, name=None, namespaced=False):
        cmd = ["kubectl", "--context", args.context]
        if namespaced:
            cmd += ["-n", namespace]
        cmd += ["get", kind] + ([name] if name else []) + ["-o", "json"]
        return json.loads(subprocess.check_output(cmd))

    crd = get("crd", "dynamographdeployments.nvidia.com")
    if not any(v["name"] == "v1beta1" and v["served"] for v in crd["spec"]["versions"]):
        raise RuntimeError("The cluster does not serve DGD v1beta1")
    nodes = get("nodes")["items"]
    records = []
    checked_pvcs = set()
    for component in dgd["spec"]["components"]:
        pod = component["podTemplate"]["spec"]
        for volume in pod["volumes"]:
            pvc = volume.get("persistentVolumeClaim", {}).get("claimName")
            if pvc and pvc not in checked_pvcs:
                if get("pvc", pvc, True).get("status", {}).get("phase") != "Bound":
                    raise RuntimeError(f"Model PVC {pvc} is not Bound")
                checked_pvcs.add(pvc)
        if pod.get("runtimeClassName"):
            get("runtimeclass", pod["runtimeClassName"])
        for name, requested in pod["containers"][0]["resources"]["limits"].items():
            if "/" not in name:
                continue
            candidates = []
            for node in nodes:
                if node.get("spec", {}).get("unschedulable"):
                    continue
                labels = node["metadata"].get("labels", {})
                if any(
                    labels.get(k) != v for k, v in pod.get("nodeSelector", {}).items()
                ):
                    continue
                if not any(
                    c["type"] == "Ready" and c["status"] == "True"
                    for c in node["status"].get("conditions", [])
                ):
                    continue
                capacity = int(node["status"].get("allocatable", {}).get(name, "0"))
                if capacity >= int(requested):
                    candidates.append(
                        {
                            "node": node["metadata"]["name"],
                            "allocatable_units": capacity,
                        }
                    )
            if not candidates:
                raise RuntimeError(
                    f"No Ready node advertises {name} >= {requested} with the required architecture"
                )
            records.append(
                {
                    "component": component["name"],
                    "resource": name,
                    "candidates": candidates,
                }
            )
    print(
        json.dumps(
            {
                "status": "prerequisites-found",
                "components": records,
                "note": "Allocatable is capacity, not free devices. Plugin sharing mode, drivers, image availability, network and actual scheduling remain to be verified.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
