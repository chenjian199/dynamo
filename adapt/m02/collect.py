#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Archive completed image checks; reject incomplete or mismatched evidence."""

import argparse
import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cpu-report", required=True, type=Path)
    parser.add_argument("--smoke-dir", default="/tmp/m02-smoke")
    args = parser.parse_args()
    image = json.loads((args.build / "image.json").read_text())
    platform = image["profile"]["id"]
    inspected = json.loads(
        subprocess.check_output(["docker", "inspect", args.container])
    )[0]
    if inspected["Image"] != image["id"]:
        raise RuntimeError("Test container does not use the recorded image")
    for mount in inspected["Mounts"]:
        if mount["Destination"] not in (
            "/models",
            "/usr/local/Ascend/driver",
            "/usr/local/dcmi",
            "/usr/local/sbin/npu-smi",
        ):
            raise RuntimeError(f"Unexpected test mount: {mount['Destination']}")
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.build / "image.json", args.output / "image.json")
    shutil.copy2(
        args.build / "image-context/artifacts/SHA256.json",
        args.output / "artifacts.json",
    )
    shutil.copy2(args.cpu_report, args.output / "cpu.json")
    harness = subprocess.check_output(
        [
            "docker",
            "exec",
            args.container,
            "/opt/dynamo/venv/bin/python",
            "-c",
            "import hashlib,json,pathlib; root=pathlib.Path('/test'); "
            "print(json.dumps({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() "
            "for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}, indent=2))",
        ]
    )
    (args.output / "test-harness.json").write_bytes(harness)
    files = {
        "installed.json": "/opt/dynamo/installed.json",
        "basic-kv.json": f"{args.smoke_dir}/result.json",
        "independent-kvbm.json": "/tmp/m02-native/result.json",
        "kvbm-baseline.json": "/tmp/m02-kvbm-baseline/summary.json",
        "kvbm-disk.json": "/tmp/m02-kvbm-disk/summary.json",
        "pd.json": "/tmp/m02-pd/summary.json",
    }
    if platform == "cuda":
        files["kvbm-nixl.json"] = "/tmp/m02-kvbm-nixl-host/summary.json"
    if platform != "ascend":
        files["gms.xml"] = "/tmp/m02-gms.xml"
    for name, source in files.items():
        subprocess.run(
            ["docker", "cp", f"{args.container}:{source}", str(args.output / name)],
            check=True,
        )
    installed = json.loads((args.output / "installed.json").read_text())
    assert not installed["introduced_dependency_errors"], installed
    cpu = json.loads((args.output / "cpu.json").read_text())
    assert cpu["no_device_sdk_imports"] and cpu["descriptor"]["platform_id"] == "cpu"
    results = {}
    for name in files:
        if name == "installed.json":
            continue
        path = args.output / name
        if name.endswith(".json"):
            result = json.loads(path.read_text())
            assert (
                result.get("passed") is True or result.get("status") == "passed"
            ), name
        else:
            suites = ET.parse(path).getroot().iter("testsuite")
            counts = [dict(suite.attrib) for suite in suites]
            assert sum(int(s["tests"]) for s in counts) == 4, counts
            assert all(
                int(s.get(key, 0)) == 0
                for s in counts
                for key in ("failures", "errors", "skipped")
            ), counts
        results[name] = "passed"
    snapshot = (args.build / "SOURCE.json").read_bytes()
    summary = {
        "status": "passed",
        "platform": platform,
        "image_id": image["id"],
        "source_snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
        "source_commit": image["source_commit"],
        "tests": results,
        "cpu_without_device_sdk": True,
        "new_dependency_conflicts": 0,
        "vendor_dependency_conflicts": len(installed["pip_check"]),
        "mounts": inspected["Mounts"],
        "raw_build_directory": str(args.build),
        "test_container": args.container,
        "deferred": ["Ascend GMS"] if platform == "ascend" else [],
        "limitations": [
            "GDS disk registration fails in this container configuration, also reproduced in the unchanged official image; local disk acceptance uses the native platform backend"
        ]
        if platform == "cuda"
        else [],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    for path in args.output.iterdir():
        if not path.name.endswith(".license"):
            path.with_name(path.name + ".license").write_text(
                "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\n"
            )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
