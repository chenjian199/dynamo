#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify complete OCI or Docker image exports without relying on cached layers."""
import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path


class HashReader:
    """Hash compressed bytes while gzip verifies and expands the stream."""

    def __init__(self, source, digest):
        self.source = source
        self.digest = digest

    def read(self, size=-1):
        data = self.source.read(size)
        self.digest.update(data)
        return data


def verify(path):
    checked = set()
    total = 0
    sizes, metadata = {}, {}
    index = None
    docker_manifest = None
    files, small_files = {}, {}
    uncompressed = {}
    # Stream once: seeking between descriptors in a multi-GB gzip file would
    # repeatedly decompress the archive and make delivery verification costly.
    with tarfile.open(path, "r|*") as archive:
        for info in archive:
            if info.name == "index.json":
                if not info.isfile() or index is not None or info.size > 2**20:
                    raise ValueError("Invalid or duplicate OCI index")
                index = json.load(archive.extractfile(info))
                continue
            if info.name == "manifest.json":
                if (
                    not info.isfile()
                    or docker_manifest is not None
                    or info.size > 2**20
                ):
                    raise ValueError("Invalid or duplicate Docker manifest")
                docker_manifest = json.load(archive.extractfile(info))
                continue
            if not info.name.startswith("blobs/sha256/") or info.isdir():
                if info.isdir():
                    continue
                if not info.isfile() or info.name in files:
                    raise ValueError(
                        f"Unexpected or duplicate archive entry: {info.name}"
                    )
                digest = hashlib.sha256()
                small = bytearray() if info.size <= 2**20 else None
                with archive.extractfile(info) as handle:
                    if info.name.endswith(".tar.gz"):
                        expanded = hashlib.sha256()
                        with gzip.GzipFile(fileobj=HashReader(handle, digest)) as layer:
                            for chunk in iter(lambda: layer.read(8 * 1024 * 1024), b""):
                                expanded.update(chunk)
                        uncompressed[info.name] = "sha256:" + expanded.hexdigest()
                        small = None
                    else:
                        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                            digest.update(chunk)
                            if small is not None:
                                small.extend(chunk)
                files[info.name] = ("sha256:" + digest.hexdigest(), info.size)
                if small is not None:
                    small_files[info.name] = small
                continue
            value = info.name.removeprefix("blobs/sha256/")
            digest = "sha256:" + value
            if not info.isfile() or len(value) != 64 or digest in sizes:
                raise ValueError(f"Invalid or duplicate blob: {info.name}")
            h = hashlib.sha256()
            small = bytearray() if info.size <= 2**20 else None
            with archive.extractfile(info) as handle:
                for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    h.update(chunk)
                    if small is not None:
                        small.extend(chunk)
            if h.hexdigest() != value:
                raise ValueError(f"Blob checksum mismatch: {digest}")
            sizes[digest] = info.size
            if small is not None:
                metadata[digest] = small

    def visit(descriptor):
        nonlocal total
        digest = descriptor["digest"]
        if sizes[digest] != descriptor["size"]:
            raise ValueError(f"Blob size mismatch: {digest}")
        if digest in checked:
            return
        checked.add(digest)
        total += sizes[digest]
        kind = descriptor["mediaType"]
        if "index" in kind or "manifest.list" in kind:
            for child in json.loads(metadata[digest])["manifests"]:
                visit(child)
        elif "manifest" in kind:
            manifest = json.loads(metadata[digest])
            for child in [manifest["config"], *manifest["layers"]]:
                visit(child)

    if index is None:
        if not docker_manifest:
            raise ValueError("Missing OCI index and Docker manifest")
        used = set()
        for image in docker_manifest:
            config_name = image["Config"]
            config_digest, _ = files[config_name]
            if (
                Path(config_name).name
                != config_digest.removeprefix("sha256:") + ".json"
                and config_name != config_digest
            ):
                raise ValueError(
                    f"Docker configuration checksum mismatch: {config_name}"
                )
            config = json.loads(small_files[config_name])
            diff_ids = config["rootfs"]["diff_ids"]
            if len(diff_ids) != len(image["Layers"]):
                raise ValueError("Docker layer count does not match configuration")
            used.add(config_name)
            for layer, expected in zip(image["Layers"], diff_ids):
                if uncompressed.get(layer, files[layer][0]) != expected:
                    raise ValueError(f"Docker layer checksum mismatch: {layer}")
                if layer.endswith(".tar.gz") and (
                    Path(layer).name.removesuffix(".tar.gz")
                    != files[layer][0].removeprefix("sha256:")
                ):
                    raise ValueError(f"Compressed layer checksum mismatch: {layer}")
                used.add(layer)
        return {
            "passed": True,
            "format": "docker",
            "images": len(docker_manifest),
            "blobs": len(used),
            "bytes": sum(files[n][1] for n in used),
        }
    for descriptor in index["manifests"]:
        visit(descriptor)
    return {"passed": True, "format": "oci", "blobs": len(checked), "bytes": total}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(verify(args.archive), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result, end="")
