#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline archive integrity tests; no Docker daemon or cached layers required."""

import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "verify_images", Path(__file__).with_name("verify-images.py")
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def sha(value):
    return hashlib.sha256(value).hexdigest()


class DeliveryTest(unittest.TestCase):
    def archive(self, entries):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "images.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, value in entries.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(value)
                tar.addfile(entry, io.BytesIO(value))
        return path

    def docker_entries(self):
        layer = b"independent complete layer bytes"
        config = json.dumps({"rootfs": {"diff_ids": ["sha256:" + sha(layer)]}}).encode()
        name = sha(config) + ".json"
        manifest = [
            {"Config": name, "RepoTags": ["test:one"], "Layers": ["layer/layer.tar"]}
        ]
        return {
            "manifest.json": json.dumps(manifest).encode(),
            name: config,
            "layer/layer.tar": layer,
        }

    def test_empty_oci_index_rejected(self):
        with self.assertRaisesRegex(ValueError, "no image manifests"):
            verifier.verify(self.archive({"index.json": b'{"manifests": []}'}))

    def test_config_blob_is_not_an_image(self):
        raw = b'{"architecture": "arm64"}'
        digest = sha(raw)
        descriptor = {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:" + digest,
            "size": len(raw),
        }
        entries = {
            "index.json": json.dumps({"manifests": [descriptor]}).encode(),
            "blobs/sha256/" + digest: raw,
        }
        with self.assertRaisesRegex(ValueError, "must identify a manifest"):
            verifier.verify(self.archive(entries))

    def test_classic_complete(self):
        report = verifier.verify(self.archive(self.docker_entries()))
        self.assertTrue(report["passed"])
        self.assertEqual(report["images"], 1)

    def test_classic_missing_layer(self):
        entries = self.docker_entries()
        del entries["layer/layer.tar"]
        with self.assertRaises(KeyError):
            verifier.verify(self.archive(entries))

    def test_classic_corrupt_layer(self):
        entries = self.docker_entries()
        entries["layer/layer.tar"] = b"corrupt"
        with self.assertRaisesRegex(ValueError, "layer checksum mismatch"):
            verifier.verify(self.archive(entries))

    def crane_entries(self):
        layer = b"independent complete layer bytes"
        compressed = gzip.compress(layer, mtime=0)
        config = json.dumps({"rootfs": {"diff_ids": ["sha256:" + sha(layer)]}}).encode()
        name = "sha256:" + sha(config)
        layer_name = sha(compressed) + ".tar.gz"
        return {
            "manifest.json": json.dumps(
                [{"Config": name, "RepoTags": ["test:one"], "Layers": [layer_name]}]
            ).encode(),
            name: config,
            layer_name: compressed,
        }

    def test_crane_compressed_layers(self):
        self.assertTrue(verifier.verify(self.archive(self.crane_entries()))["passed"])

    def test_crane_wrong_expanded_layer(self):
        entries = self.crane_entries()
        name = next(n for n in entries if n.endswith(".tar.gz"))
        entries[name] = gzip.compress(b"different bytes", mtime=0)
        with self.assertRaisesRegex(ValueError, "layer checksum mismatch"):
            verifier.verify(self.archive(entries))

    def test_crane_wrong_compressed_digest(self):
        entries = self.crane_entries()
        name = next(n for n in entries if n.endswith(".tar.gz"))
        entries[name] = gzip.compress(b"independent complete layer bytes", mtime=1)
        with self.assertRaisesRegex(ValueError, "Compressed layer checksum mismatch"):
            verifier.verify(self.archive(entries))

    def test_oci_complete_and_missing_blob(self):
        entries = {}

        def blob(value, kind):
            data = json.dumps(value).encode()
            digest = sha(data)
            entries["blobs/sha256/" + digest] = data
            return {"mediaType": kind, "digest": "sha256:" + digest, "size": len(data)}

        config = blob(
            {"architecture": "amd64"}, "application/vnd.oci.image.config.v1+json"
        )
        manifest = blob(
            {"config": config, "layers": []},
            "application/vnd.oci.image.manifest.v1+json",
        )
        entries["index.json"] = json.dumps({"manifests": [manifest]}).encode()
        self.assertTrue(verifier.verify(self.archive(entries))["passed"])
        del entries["blobs/" + config["digest"].replace(":", "/")]
        with self.assertRaisesRegex(ValueError, "Missing referenced OCI blob"):
            verifier.verify(self.archive(entries))


if __name__ == "__main__":
    unittest.main()
