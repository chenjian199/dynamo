#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
: "${PLATFORM:?metax or ascend}"
: "${DEVICE:?visible device ordinal}"
: "${OUTPUT:?fresh output directory}"
mkdir "$OUTPUT"
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
case "$PLATFORM" in
    metax)
        g++ -DMETAX "$root/adapter/m07/probe-vmm.cpp" \
            -I/opt/maca/include -I/opt/maca/include/mcr \
            -L/opt/maca/lib -lmcruntime -o "$OUTPUT/probe-vmm"
        modes=(1 2)
        ;;
    ascend)
        cann="${CANN_ROOT:-/usr/local/Ascend/cann-9.1.0/aarch64-linux}"
        g++ "$root/adapter/m07/probe-vmm.cpp" -I"$cann/include" \
            -L"$cann/lib64" -lascendcl -o "$OUTPUT/probe-vmm"
        export ASCEND_SLOG_PRINT_TO_STDOUT=1 ASCEND_GLOBAL_LOG_LEVEL=1
        # Default handles: RW→RO, fresh RO, and unmap/remap→RO; mode 1 tests POSIX.
        modes=(0 1 2 3)
        ;;
    *) exit 2 ;;
esac
"${DYNAMO_VENV:-/opt/dynamo/venv}/bin/python" - "$root" "$OUTPUT" <<'PY'
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

root, output = map(Path, sys.argv[1:])
files = [root / "adapter/m07/probe-vmm.cpp", root / "adapter/m07/probe.sh", output / "probe-vmm"]
info = {
    "started": datetime.now(timezone.utc).isoformat(),
    "platform": os.environ["PLATFORM"],
    "device": os.environ["DEVICE"],
    "architecture": platform.machine(),
    "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    "packages": {},
}
for name in ("vllm", "vllm-ascend", "vllm-metax", "torch", "torch-npu"):
    try:
        info["packages"][name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        pass
if info["platform"] == "ascend":
    info["driver"] = Path("/usr/local/Ascend/driver/version.info").read_text()
    info["cann"] = Path("/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/ascend_toolkit_install.info").read_text()
    lib = Path("/usr/local/Ascend/driver/lib64/driver/libascend_hal.so")
    info["hal_sha256"] = hashlib.sha256(lib.read_bytes()).hexdigest()
(output / "environment.json").write_text(json.dumps(info, indent=2) + "\n")
PY
for mode in "${modes[@]}"; do
    if timeout --kill-after=5 30 "$OUTPUT/probe-vmm" "$DEVICE" "$mode" > "$OUTPUT/mode-$mode.txt" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    printf '%s\n' "$rc" > "$OUTPUT/mode-$mode.exit"
done
# Exit codes per mode are evidence: 4 = access contract not met,
# 1 = SDK/export failure, 3 = cleanup failure, 124 = timeout.
