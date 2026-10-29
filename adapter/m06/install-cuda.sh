#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# Run inside the CUDA dev container after its regression servers have stopped.
# Supply the x86_64 wheel built from this checkout by maturin.
wheel=${1:?Usage: install-cuda.sh /path/to/kvbm.whl}
test -f "$wheel"
base=${DYNAMO_PYTHON:-/opt/dynamo/venv/bin/python}
venv=${M06_VENV:-/opt/dynamo/m06-venv}
"$base" -m venv --system-site-packages "$venv"
export M06_BASE_SITE
M06_BASE_SITE=$("$base" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
"$venv/bin/python" - <<'PY'
import os
import sysconfig
from pathlib import Path

site = Path(sysconfig.get_path("purelib"))
base = os.environ["M06_BASE_SITE"]
(site / "dynamo-base.pth").write_text(f"import site; site.addsitedir({base!r})\n")
PY
# Keep the original M03/M05 environment intact. The Rust KVBM binding's C API
# requires this NIXL pair; do not resolve dependencies over vendor torch/vLLM.
"$venv/bin/python" -m pip install --no-deps --force-reinstall \
    "$wheel" 'nixl==1.3.2' 'nixl-cu13==1.3.2'
DYN_PLATFORM=cuda "$venv/bin/python" -c \
    'import importlib.metadata as m; import kvbm; print({n: m.version(n) for n in ("torch", "vllm", "kvbm", "nixl", "nixl-cu13")})'
