#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# Run inside the existing vendor dev container. Does not upgrade torch/vLLM/NIXL.
platform=${1:?Usage: build.sh cuda|metax|ascend}
case "$platform" in cuda|metax|ascend) ;; *) exit 2 ;; esac
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
export PATH="/root/.cargo/bin:/opt/dynamo/venv/bin:$PATH"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-/var/cache/dynamo-${platform}/target}"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-16}"
export CARGO_PROFILE_DEV_DEBUG=0
# This selects compile-time cudarc definitions. Vendor host staging never calls
# these CUDA APIs; its runtime SDK is selected explicitly from the tensor device.
export CUDARC_CUDA_VERSION=13000
if [[ $(uname -m) == aarch64 ]]; then
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS='-C target-cpu=generic -C force-frame-pointers=yes --cfg tokio_unstable'
fi
build_args='--profile dev --locked'
if [[ ${ONLINE:-0} != 1 ]]; then
    build_args+=' --offline'
fi
/opt/dynamo/venv/bin/python -m pip install --no-deps --no-build-isolation \
    -e lib/bindings/kvbm --config-settings="build-args=$build_args"
DYN_PLATFORM="$platform" /opt/dynamo/venv/bin/python -c \
    'import kvbm; print("KVBM Python:", kvbm.__file__); print("KVBM Rust:", kvbm._core.__file__)'
