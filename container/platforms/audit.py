#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Record and verify incremental image inputs without editing vendor metadata."""

import hashlib
import importlib
import importlib.metadata as metadata
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def snapshot():
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True
    )
    return {
        "packages": {
            name: metadata.version(name)
            for name in {
                p.metadata["Name"].lower().replace("_", "-")
                for p in metadata.distributions()
            }
        },
        "pip_check_exit": result.returncode,
        "pip_check": sorted(set(result.stdout.splitlines()))
        if result.returncode
        else [],
        "pip_check_stderr": result.stderr,
    }


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main():
    action, *arguments = sys.argv[1:]
    path = Path(arguments[0])
    if action == "snapshot":
        dump(path, snapshot())
    elif action == "constraints":
        profile = json.loads(Path(f"container/platforms/{path}.json").read_text())
        packages = snapshot()["packages"]
        for name, version in profile["versions"].items():
            if packages.get(name) != version:
                raise RuntimeError(
                    f"Unexpected base {name}: {packages.get(name)} != {version}"
                )
        Path(arguments[1]).write_text(
            "\n".join(
                f"{name}=={packages[name]}"
                for name in profile["protected_packages"]
                if name in packages
            )
            + "\n"
        )
    elif action == "download":
        destination = Path(arguments[1])
        for package in json.loads(path.read_text())["install"]:
            info = package["download_info"]
            url = info["url"]
            name = url.rsplit("/", 1)[-1].split("?")[0]
            if not name.endswith(".whl"):
                raise RuntimeError(f"Runtime dependency is not a binary wheel: {name}")
            target = destination / name
            urllib.request.urlretrieve(url, target)
            expected = info["archive_info"]["hashes"]["sha256"]
            if digest(target) != expected:
                raise RuntimeError(f"Dependency hash mismatch: {name}")
    elif action == "native":
        destination = Path(arguments[1])
        libraries, binaries = [], []
        for line in path.read_text().splitlines():
            item = json.loads(line)
            if (
                item.get("reason") == "build-script-executed"
                and "kvbm-kernels" in item["package_id"]
            ):
                libraries.append(Path(item["out_dir"]) / "libkvbm_kernels.so")
            if (
                item.get("reason") == "compiler-artifact"
                and item["target"]["name"] == "native_replicated"
                and item.get("executable")
            ):
                binaries.append(Path(item["executable"]))
        if len(libraries) != 1 or len(binaries) != 1:
            raise RuntimeError(
                "Expected one KVBM helper and one native diagnostic executable"
            )
        for artifact in libraries + binaries:
            shutil.copy2(artifact, destination / artifact.name)
    elif action == "seal":
        dump(
            path / "SHA256.json",
            {
                str(p.relative_to(path)): digest(p)
                for p in sorted(path.rglob("*"))
                if p.is_file() and p.name != "SHA256.json"
            },
        )
    elif action == "verify":
        manifest = json.loads((path / "SHA256.json").read_text())
        actual = {
            str(p.relative_to(path))
            for p in path.rglob("*")
            if p.is_file() and p != path / "SHA256.json"
        }
        if actual != set(manifest):
            raise RuntimeError("Artifact file set differs from manifest")
        for name, expected in manifest.items():
            target = (path / name).resolve()
            if not target.is_relative_to(path.resolve()) or digest(target) != expected:
                raise RuntimeError(f"Artifact verification failed: {name}")
    elif action == "installed":
        base = json.loads((path / "base.json").read_text())
        profile = json.loads((path / "profile.json").read_text())
        current = snapshot()
        for name in profile["protected_packages"]:
            if current["packages"].get(name) != base["packages"].get(name):
                raise RuntimeError(f"Protected vendor package changed: {name}")
        introduced = sorted(set(current["pip_check"]) - set(base["pip_check"]))
        current["introduced_dependency_errors"] = introduced
        current["mode"] = "no-new-conflicts; vendor baseline issues retained"
        locations = {}
        for name in (
            "dynamo._core",
            "dynamo.common.platform",
            "kvbm._core",
            "gpu_memory_service",
        ):
            module = importlib.import_module(name)
            location = str(Path(module.__file__).resolve())
            if not location.startswith("/opt/dynamo/venv/"):
                raise RuntimeError(
                    f"Not using packaged artifact for {name}: {location}"
                )
            locations[name] = location
        current["imports"] = locations
        for name in ("ai-dynamo", "ai-dynamo-runtime", "kvbm", "gpu-memory-service"):
            direct = metadata.distribution(name).read_text("direct_url.json")
            if direct and json.loads(direct).get("dir_info", {}).get("editable"):
                raise RuntimeError(f"Editable installation found: {name}")
        dump(path.parent / "installed.json", current)
        if introduced:
            raise RuntimeError(f"New dependency conflicts: {introduced}")
        print(
            json.dumps(
                {"imports": locations, "vendor_conflicts": len(current["pip_check"])}
            )
        )
    else:
        raise ValueError(action)


if __name__ == "__main__":
    main()
