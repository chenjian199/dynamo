#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Preserve completed Profiler output on its node before deleting the DGDR."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--namespace", default="dynamo-m08-1029")
p.add_argument("--name", default="m09-profile")
p.add_argument("--output", type=Path, required=True)
p.add_argument("--archive", type=Path, required=True)
p.add_argument(
    "--source", type=Path, help="Persistent profiling-output directory on this node"
)
p.add_argument("--kubelet-root", type=Path, default=Path("/var/lib/kubelet"))
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
k = ["kubectl", "-n", a.namespace]
dgdr = json.loads(subprocess.check_output(k + ["get", "dgdr", a.name, "-o", "json"]))
job = dgdr["status"]["profilingJobName"]
pods = json.loads(
    subprocess.check_output(k + ["get", "pods", "-l", "job-name=" + job, "-o", "json"])
)["items"]
assert len(pods) == 1, "Collect individual attempts explicitly when a Job retried"
pod = pods[0]
assert pod["status"]["phase"] in {
    "Succeeded",
    "Failed",
}, "Wait for Profiler termination before archiving"
source = a.source or (
    a.kubelet_root
    / "pods"
    / pod["metadata"]["uid"]
    / "volumes/kubernetes.io~empty-dir/profiling-output"
)
assert source.is_dir(), "Run on the Profiler node with read access to its output volume"
files = [f for f in source.rglob("*") if f.is_file() and not f.is_symlink()]
archive_name = a.archive.resolve()
archive_name.parent.mkdir(parents=True, exist_ok=True)
with tarfile.open(archive_name, "w:gz") as archive:
    archive.add(source, arcname="profiling-output")

selected = []
for path in files:
    if path.suffix in {".yaml", ".yml"} or path.name in {
        "profile_export_aiperf.json",
        "profile-identity.json",
        "enumeration-capacity.json",
        "profile_sla.log",
    }:
        target = a.output / "profiler" / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        selected.append(str(path.relative_to(source)))
for container in pod["spec"]["containers"]:
    log = subprocess.check_output(
        k + ["logs", pod["metadata"]["name"], "-c", container["name"]], timeout=60
    )
    (a.output / (container["name"] + ".log")).write_bytes(log)
for filename, obj in [("dgdr.json", dgdr), ("profiler-pod.json", pod)]:
    (a.output / filename).write_text(json.dumps(obj, indent=2))
with archive_name.open("rb") as stream:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
receipt = {
    "node": pod["spec"]["nodeName"],
    "pod_uid": pod["metadata"]["uid"],
    "archive": str(archive_name),
    "bytes": archive_name.stat().st_size,
    "sha256": digest.hexdigest(),
    "raw_file_count": len(files),
    "selected_files": selected,
}
(a.output / "archive.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt), flush=True)
