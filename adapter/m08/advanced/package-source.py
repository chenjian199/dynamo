#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Package this M08 source overlay, with per-file hashes, without vendor trees."""

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
scopes = [
    "deploy/operator",
    "deploy/utils",
    "deploy/helm/charts/platform/components/operator",
    "container/platforms",
    "adapter/m08",
    "adapter/m02/cluster",
    "adapter/README.md",
    "adapter/modules.md",
    "adapter/support-matrix.md",
    "adapter/modules/m08-kubernetes",
    "components/src/dynamo/profiler",
    "components/src/dynamo/common/platform",
    "components/src/dynamo/vllm/kv_connector_protocols.py",
]
paths = (
    subprocess.check_output(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *scopes,
        ]
    )
    .decode()
    .split("\0")
)
files = sorted(
    {
        p
        for p in paths
        if p
        and Path(p).is_file()
        and not p.startswith("adapter/m08/advanced/delivery.json")
    }
)
manifest = {
    "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"])
    .decode()
    .strip(),
    "scope": "M08 working-tree overlay; apply to this base repository, not a standalone checkout",
    "file_count": len(files),
    "sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files},
}
a.output.parent.mkdir(parents=True, exist_ok=True)
with tarfile.open(a.output, "w:gz") as archive:
    for path in files:
        archive.add(path, arcname=path, recursive=False)
    for name, data in [
        ("SOURCE.json", (json.dumps(manifest, indent=2) + "\n").encode()),
        (
            "SOURCE.json.license",
            b"SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\n",
        ),
    ]:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(data))
digest = hashlib.sha256(a.output.read_bytes()).hexdigest()
Path(str(a.output) + ".sha256").write_text(f"{digest}  {a.output.name}\n")
print(
    json.dumps(
        {
            "archive": str(a.output),
            "bytes": a.output.stat().st_size,
            "sha256": digest,
            "files": len(files),
        }
    )
)
