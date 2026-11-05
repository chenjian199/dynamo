#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Transfer only absent OCI layers to known test nodes; not a release bundle.

The final offline delivery always contains complete images. This temporary
transport archive requires every omitted digest to exist on each target node.
Image manifests/configs and their digests are retained unchanged.
"""
import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


def blob_name(digest):
    algorithm, value = digest.split(":", 1)
    if (
        algorithm != "sha256"
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"Unsupported digest: {digest}")
    return f"blobs/sha256/{value}"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--present", type=Path, action="append", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--rename", help="Optional temporary image reference for an import test"
    )
    a = p.parse_args()
    if a.output.exists():
        p.error("Output already exists")
    present = set.intersection(*(set(path.read_text().split()) for path in a.present))
    omitted, included, descriptors = {}, {}, {}
    with tarfile.open(a.input) as source:
        index = json.load(source.extractfile("index.json"))
        if a.rename:
            if len(index["manifests"]) != 1:
                raise ValueError("Rename requires a single image")
            index["manifests"][0]["annotations"] = {
                "io.containerd.image.name": a.rename,
                "org.opencontainers.image.ref.name": a.rename,
            }

        def visit(descriptor):
            digest = descriptor["digest"]
            if digest in descriptors:
                return
            name = blob_name(digest)
            info = source.getmember(name)
            if info.size != descriptor["size"]:
                raise ValueError(f"Blob size mismatch: {digest}")
            descriptors[digest] = descriptor
            kind = descriptor["mediaType"]
            if "layer." in kind or "rootfs.diff" in kind:
                (omitted if digest in present else included)[digest] = info.size
                return
            included[digest] = info.size
            raw = source.extractfile(name).read()
            if hashlib.sha256(raw).hexdigest() != digest.split(":")[1]:
                raise ValueError(f"Metadata hash mismatch: {digest}")
            if "index" in kind or "manifest.list" in kind:
                for child in json.loads(raw)["manifests"]:
                    visit(child)
            elif "manifest" in kind:
                manifest = json.loads(raw)
                for child in [manifest["config"], *manifest["layers"]]:
                    visit(child)

        for descriptor in index["manifests"]:
            visit(descriptor)
        partial = a.output.with_suffix(a.output.suffix + ".partial")
        with tarfile.open(partial, "w:gz", compresslevel=1) as target:
            for name, obj in [
                ("oci-layout", {"imageLayoutVersion": "1.0.0"}),
                ("index.json", index),
            ]:
                raw = json.dumps(obj).encode()
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                target.addfile(info, io.BytesIO(raw))
            for digest in included:
                name = blob_name(digest)
                target.addfile(source.getmember(name), source.extractfile(name))
        partial.rename(a.output)
    receipt = {
        "kind": "temporary-oci-delta-not-offline-bundle",
        "input": str(a.input),
        "required_existing_layers": omitted,
        "included_blobs": included,
        "manifest_descriptors": index["manifests"],
    }
    a.output.with_suffix(a.output.suffix + ".json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print(
        f"Included {sum(included.values())} bytes; reused {sum(omitted.values())} bytes"
    )


if __name__ == "__main__":
    main()
