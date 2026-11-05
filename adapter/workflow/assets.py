# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Standalone model-asset probe; also sent over SSH without installing the workflow."""

import hashlib
import json
import sys
from pathlib import Path


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inspect_model(model):
    if not isinstance(model, dict) or not all(
        model.get(k) for k in ("id", "path", "revision", "manifest", "manifest_sha256")
    ):
        raise ValueError(
            "Model requires id, path, revision and a SHA256-pinned file manifest"
        )
    root = Path(model["path"]).resolve()
    manifest = (root / model["manifest"]).resolve()
    if (
        not manifest.is_relative_to(root)
        or sha256(manifest) != model["manifest_sha256"]
    ):
        raise ValueError("Model manifest path or checksum mismatch")
    data = json.loads(manifest.read_text())
    if data.get("model_id") != model["id"] or data.get("revision") != model["revision"]:
        raise ValueError("Model revision differs from frozen manifest")
    files = data.get("files", {})
    if "config.json" not in files or not any(
        name.endswith((".safetensors", ".bin", ".gguf")) for name in files
    ):
        raise ValueError("Model manifest must include config and weights")
    for name, expected in files.items():
        file = (root / name).resolve()
        if (
            not file.is_relative_to(root)
            or not isinstance(expected, str)
            or sha256(file) != expected
        ):
            raise ValueError("Model asset unreadable or checksum mismatch: " + name)
    for index in root.glob("*.index.json"):
        weight_map = json.loads(index.read_text()).get("weight_map")
        if weight_map is not None and (
            index.name not in files or not set(weight_map.values()).issubset(files)
        ):
            raise ValueError("Model manifest omits weight index or shards")
    return {
        "model_id": model["id"],
        "revision": model["revision"],
        "manifest_sha256": model["manifest_sha256"],
        "checked_files": len(files),
    }


if __name__ == "__main__":
    try:
        print(json.dumps(inspect_model(json.loads(sys.argv[1]))))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
