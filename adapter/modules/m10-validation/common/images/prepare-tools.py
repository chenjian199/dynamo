#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Freeze a tools-image build context from an explicit release source checkout."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--platform", choices=["cuda", "ascend", "metax"], required=True)
p.add_argument("--kubectl", type=Path, required=True)
p.add_argument("--override-bin", type=Path, required=True)
p.add_argument("--aisimulate-wheel", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument(
    "--extra-wheel",
    type=Path,
    action="append",
    default=[],
    help="Audited native dependency wheel; included in SOURCE.json",
)
a = p.parse_args()
expected = (
    "3f3330d5d4521b0e5ee5fd11a649e3be8d971c0dc41079af7a82d7034c6b0e89"
    if a.platform == "ascend"
    else "3d3815659bd6d692d747f551d3bc3d71b9b2cb206bb33d783bdbe245c389bdb0"
)
# Published SHA256 values from https://pypi.nvidia.com/aisimulate/ for 0.13.0 (Dynamo 1.6.2).
if hashlib.sha256(a.aisimulate_wheel.read_bytes()).hexdigest() != expected:
    p.error("AISimulate wheel does not match the pinned official hash")
a.output.mkdir(parents=True, exist_ok=False)
(a.output / "wheels").mkdir()
shutil.copy2(a.aisimulate_wheel, a.output / "wheels" / a.aisimulate_wheel.name)
for wheel in a.extra_wheel:
    if wheel.suffix != ".whl":
        p.error("extra-wheel must be a wheel archive")
    shutil.copy2(wheel, a.output / "wheels" / wheel.name)
files = {
    "container/platforms/audit.py": "audit.py",
    f"container/platforms/{a.platform}.json": "profile.json",
    "adapter/platforms/metax/cluster/requirements-tools.txt": "requirements-tools.txt",
    f"adapter/modules/m09-telemetry-planner/common/packaging/requirements-planner-{'ascend' if a.platform == 'ascend' else 'cuda'}.txt": "requirements-planner.txt",
    "adapter/modules/m08-kubernetes/common/advanced/patch-aiperf.py": "patch-aiperf.py",
    "adapter/modules/m09-telemetry-planner/common/packaging/patch-aiperf-local-fast.py": "patch-aiperf-local-fast.py",
}
for source, target in files.items():
    shutil.copy2(a.source / source, a.output / target)
for name in [
    "tools.Dockerfile",
    "tools-ascend.Dockerfile",
    "tools-entrypoint.sh",
    "capacity-links.py",
    "requirements-tools-constraints.txt",
]:
    shutil.copy2(Path(__file__).with_name(name), a.output / name)
if a.platform == "ascend":
    shutil.copy2(a.output / "tools-ascend.Dockerfile", a.output / "tools.Dockerfile")
shutil.copytree(
    a.source / "deploy/utils",
    a.output / "deploy/utils",
    ignore=shutil.ignore_patterns("__pycache__"),
)
for source, target in [(a.kubectl, "kubectl"), (a.override_bin, "dgd-apply-overrides")]:
    shutil.copy2(source, a.output / target)
    (a.output / target).chmod(0o755)
manifest = {
    str(f.relative_to(a.output)): hashlib.sha256(f.read_bytes()).hexdigest()
    for f in sorted(a.output.rglob("*"))
    if f.is_file()
}
(a.output / "SOURCE.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(a.output)
