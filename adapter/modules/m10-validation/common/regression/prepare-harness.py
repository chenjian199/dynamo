#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Copy test drivers only; never copy Dynamo implementation into test Pods."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument(
    "--test-deps",
    type=Path,
    required=True,
    help="Isolated pure-Python pytest plugins, with no Dynamo or engine packages",
)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
allowed = ("pytest_asyncio", "pytest_timeout", "bin")
if any(not child.name.startswith(allowed) for child in a.test_deps.iterdir()):
    p.error("Test dependency directory contains an unexpected package")
shutil.copytree(a.test_deps, a.output / "test-deps")
files = [
    "adapter/modules/m03-engine-integration/common/engine/serve.sh",
    "adapter/modules/m03-engine-integration/common/engine/rank-smoke.py",
    "adapter/modules/m03-engine-integration/common/engine/advanced-smoke.py",
    "adapter/modules/m03-engine-integration/common/engine/create-lora.py",
    "adapter/modules/m03-engine-integration/common/engine/create-quantized.py",
    "adapter/platforms/metax/bringup/metax-smoke.py",
    "adapter/platforms/metax/bringup/metax-kv-smoke.py",
    "examples/common/gpu_utils.sh",
    "examples/common/launch_utils.sh",
    "adapter/modules/m05-pd-transfer/common/transfer/run.py",
    "adapter/modules/m05-pd-transfer/common/transfer/patch_vendor.py",
    "adapter/modules/m05-pd-transfer/common/transfer/topology.py",
    "adapter/modules/m05-pd-transfer/common/transfer/handoff.py",
    "adapter/modules/m05-pd-transfer/common/transfer/soak.py",
    "adapter/modules/m05-pd-transfer/common/transfer/observer.py",
    "adapter/modules/m05-pd-transfer/common/transfer/inflight_engine.py",
    "adapter/modules/m06-kvbm-collectives/common/cache/run.py",
    "adapter/modules/m06-kvbm-collectives/common/cache/sources.txt",
    "adapter/modules/m07-gms-multimodal/common/media/test_multimodal.py",
    "adapter/modules/m07-gms-multimodal/common/media/red.webm",
    "adapter/modules/m07-gms-multimodal/common/media/red.webm.license",
    "lib/gpu_memory_service/tests/test_platform_vmm.py",
    "container/platforms/cpu-smoke.py",
    "container/platforms/smoke.py",
    "container/platforms/native-smoke.py",
    "tests/__init__.py",
    "tests/utils/managed_process.py",
    "tests/utils/port_utils.py",
    "tests/utils/constants.py",
    "tests/utils/test_output.py",
]
for name in files:
    target = a.output / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a.source / name, target)
shutil.copy2(
    Path(__file__).with_name("run-engine.py"), a.output / "adapter/modules/m03-engine-integration/common/engine/run-case.py"
)
shutil.copytree(a.source / "adapter/modules/m05-pd-transfer/common/transfer/patches", a.output / "adapter/modules/m05-pd-transfer/common/transfer/patches")
for name in (
    "check-installed.py",
    "run-hardware.sh",
    "run-advanced.sh",
    "run-pd.sh",
    "run-tools.sh",
):
    shutil.copy2(Path(__file__).with_name(name), a.output / name)
(a.output / "pytest.ini").write_text(
    "# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\n"
    "# SPDX-License-Identifier: Apache-2.0\n"
    "[pytest]\nasyncio_mode=auto\naddopts=--strict-markers\nmarkers=\n"
    "    post_merge: device regression\n    integration: real component integration\n"
    "    gpu_1: one accelerator\n    asyncio: asynchronous case\n    timeout: bounded test\n"
)
manifest = {
    str(f.relative_to(a.output)): hashlib.sha256(f.read_bytes()).hexdigest()
    for f in sorted(a.output.rglob("*"))
    if f.is_file()
}
(a.output / "SOURCE.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(a.output)
