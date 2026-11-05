#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Archive only campaign manifests, selected Job logs and resource snapshots; exclude SSH material."""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
files = {}
for path in sorted(a.source.glob("*.json")):
    try:
        obj = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    if not isinstance(obj, dict):
        continue
    items = obj.get("items", []) if obj.get("kind") == "List" else [obj]
    if not items or not all(
        isinstance(x, dict) and x.get("apiVersion") and x.get("kind") for x in items
    ):
        continue
    if any(x["kind"] in ("Secret", "Pod") for x in items):
        continue
    if not all(
        x.get("metadata", {}).get("labels", {}).get("campaign") == a.namespace
        for x in items
    ):
        continue
    files["manifests/" + path.name] = path
for platform in ("cuda", "ascend"):
    for folder in (
        platform + "-job-logs",
        platform + "-worker-logs",
        platform + "-pd-observed-resources",
    ):
        for path in sorted((a.source / folder).glob("*")):
            if path.is_file() and path.suffix in (".json", ".log"):
                files[folder + "/" + path.name] = path
for name in (
    "cuda-disk-failure-worker.log",
    "cuda-large-pd-decode-0-error.log",
    "cuda-large-pd-prefill-0-error.log",
    "cuda-reverse-port-conflict.log",
    "cuda-distributed-worker-0.log",
    "cuda-distributed-worker-1.log",
    "ascend-distributed-worker-0.log",
    "ascend-distributed-worker-1.log",
    "cuda-deepseek-worker.log",
    "ascend-native-repeat-env-error.log",
):
    path = a.source / name
    if path.is_file():
        files["diagnostics/" + name] = path
archive = a.output / "evidence.tar.gz"
with tarfile.open(archive, "w:gz") as tar:
    for name, path in sorted(files.items()):
        tar.add(path, arcname=name, recursive=False)
index = {
    "namespace": a.namespace,
    "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "qualification": "Exact captured manifests/logs; some earlier worker Pods were scaled down before a complete Pod provenance snapshot. Job provenance does not substitute for worker provenance.",
    "files": [
        {
            "path": name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in sorted(files.items())
    ],
}
(a.output / "evidence-index.json").write_text(json.dumps(index, indent=2) + "\n")
license = "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\nArchive preserves notices in embedded source files.\n"
for name in ("evidence.tar.gz", "evidence-index.json"):
    (a.output / (name + ".license")).write_text(license)
print(
    json.dumps(
        {
            "files": len(files),
            "archive_bytes": archive.stat().st_size,
            "sha256": index["archive_sha256"],
        }
    )
)
