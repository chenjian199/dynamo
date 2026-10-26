#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
source_root=${DYNAMO_SOURCE:-/workspace/dynamo}
mode=${1:-all}
case "$mode" in all|python) ;; *) echo 'Usage: dev-editable.sh [all|python]' >&2; exit 2 ;; esac
cd "$source_root"
# Reject a checkout targeting a different vendor stack before touching the venv.
python - <<'PY'
import json, os, tomllib
from pathlib import Path
platform=os.environ['DYNAMO_IMAGE_PLATFORM']
a=json.loads(Path(f'container/platforms/{platform}.json').read_text())
b=json.loads(Path('/opt/dynamo/artifacts/profile.json').read_text())
if a != b:
    raise RuntimeError('Source platform profile differs from the runtime image; rebuild the image')
a=tomllib.loads(Path('rust-toolchain.toml').read_text())
b=tomllib.loads(Path('/opt/dynamo/dev/rust-toolchain.toml').read_text())
if a != b:
    raise RuntimeError('Source Rust toolchain differs from the dev image; rebuild the image')
PY
source /opt/dynamo/venv/bin/activate
export PIP_NO_DEPS=1 PIP_DISABLE_PIP_VERSION_CHECK=1
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-12} CARGO_PROFILE_RELEASE_DEBUG=0 CUDARC_CUDA_VERSION=13000
export SWAGGER_UI_DOWNLOAD_URL=file:///opt/dynamo/dev/downloads/swagger-ui-v5.17.14.zip
unset CONDA_PREFIX
if [[ $(uname -m) == aarch64 ]]; then
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS='-C target-cpu=generic -C force-frame-pointers=yes --cfg tokio_unstable'
fi
if [[ $DYNAMO_IMAGE_PLATFORM == cuda ]]; then
    export KVBM_REQUIRE_CUDA=1 CUDA_ARCHS=${CUDA_ARCHS:-90}
fi
if [[ $mode == all ]]; then
    runtime_features=$(python -c 'import json; print(",".join(json.load(open("/opt/dynamo/artifacts/profile.json"))["runtime_features"]))')
    kvbm_features=$(python -c 'import json; print(",".join(json.load(open("/opt/dynamo/artifacts/profile.json"))["kvbm_features"]))')
    maturin develop --release --locked --manifest-path lib/bindings/python/Cargo.toml --features "$runtime_features"
    # Both binding crates output lib_core.so. Sharing a target directory can
    # install the other crate's library on a cached/repeated build.
    maturin develop --release --locked --manifest-path lib/bindings/kvbm/Cargo.toml \
        --target-dir "${CARGO_TARGET_DIR:-/build-cache/target}-kvbm" --features "$kvbm_features"
    # Refresh the independent physical-path executable and its exact C ABI helper.
    mkdir -p /build-cache
    cargo build --release --locked -p kvbm-engine --example native_replicated \
        --no-default-features --features collectives --message-format=json > /build-cache/native-build.jsonl
    python /opt/dynamo/bin/audit.py native /build-cache/native-build.jsonl /opt/dynamo/native
fi
python -m pip install --no-deps --no-build-isolation -e . -e lib/gpu_memory_service
python /opt/dynamo/bin/dev-check.py
DYNAMO_EDITABLE_MODE="$mode" python - <<'PY'
import importlib, os
from pathlib import Path
modules=['dynamo.common.platform', 'gpu_memory_service']
if os.environ['DYNAMO_EDITABLE_MODE'] == 'all':
    modules += ['dynamo._core', 'kvbm._core']
for name in modules:
    path=Path(importlib.import_module(name).__file__).resolve()
    if not path.is_relative_to(Path.cwd()):
        raise RuntimeError(f'Editable import was shadowed: {name}: {path}')
    print(name, path)
PY
