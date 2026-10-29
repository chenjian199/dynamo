#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
cd /src
platform=${1:?platform}
export DEBIAN_FRONTEND=noninteractive
mkdir -p /out/wheels /out/native
python3 container/platforms/audit.py snapshot /out/base.json
apt-get update
apt-get install -y --no-install-recommends build-essential pkg-config cmake protobuf-compiler \
    libprotobuf-dev libssl-dev libclang-dev libudev-dev libnuma-dev libzmq3-dev git curl ca-certificates patch
python3 -m venv --system-site-packages /opt/dynamo/build
export CARGO_HOME=/build-cache/cargo RUSTUP_HOME=/build-cache/rustup
export PATH=/opt/dynamo/build/bin:$CARGO_HOME/bin:$PATH
python -m pip install -r container/requirements/platforms/build.txt
python container/platforms/audit.py constraints "$platform" /out/protected.txt
# Resolve runtime additions separately so build-only packages cannot satisfy runtime dependencies.
python3 -m venv --clear --system-site-packages /opt/dynamo/resolve
/opt/dynamo/resolve/bin/python -m pip install --report /out/dependencies.json \
    -c /out/protected.txt -r container/requirements/platforms/runtime.txt
python container/platforms/audit.py download /out/dependencies.json /out/wheels
if [[ ${DYNAMO_RUST_MIRROR:-} == rsproxy ]]; then
    export RUSTUP_DIST_SERVER=https://rsproxy.cn RUSTUP_UPDATE_ROOT=https://rsproxy.cn/rustup
fi
if ! command -v rustup >/dev/null; then
    curl --fail --location --retry 3 https://sh.rustup.rs -o /tmp/rustup.sh
    sh /tmp/rustup.sh -y --profile minimal --default-toolchain none
fi
rust_version=$(python -c 'import tomllib; print(tomllib.load(open("rust-toolchain.toml", "rb"))["toolchain"]["channel"])')
rustup toolchain install "$rust_version" --profile minimal
export CARGO_TARGET_DIR=/build-cache/target CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-12}
export CARGO_PROFILE_RELEASE_DEBUG=0 CUDARC_CUDA_VERSION=13000
unset CONDA_PREFIX
if [[ $(uname -m) == aarch64 ]]; then
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS='-C target-cpu=generic -C force-frame-pointers=yes --cfg tokio_unstable'
fi
if [[ $platform == cuda ]]; then
    export KVBM_REQUIRE_CUDA=1 CUDA_ARCHS=${CUDA_ARCHS:-90}
fi
runtime_features=$(python -c 'import json,sys; print(",".join(json.load(open("container/platforms/"+sys.argv[1]+".json"))["runtime_features"]))' "$platform")
kvbm_features=$(python -c 'import json,sys; print(",".join(json.load(open("container/platforms/"+sys.argv[1]+".json"))["kvbm_features"]))' "$platform")
# utoipa otherwise downloads from GitHub inside its build.rs without retries.
mkdir -p /build-cache/downloads
swagger=/build-cache/downloads/swagger-ui-v5.17.14.zip
if [[ ! -f $swagger ]]; then
    curl --http1.1 --fail --location --retry 3 --max-time 120 \
        https://codeload.github.com/swagger-api/swagger-ui/zip/refs/tags/v5.17.14 -o "$swagger.tmp"
    mv "$swagger.tmp" "$swagger"
fi
printf '%s  %s\n' 481244d0812097b11fbaeef79f71d942b171617f9c9f9514e63acbe13e71ccdc "$swagger" | sha256sum -c -
export SWAGGER_UI_DOWNLOAD_URL="file://$swagger"
# Keep builds locked, including all path dependencies, and package both Rust entrypoints.
maturin build --manifest-path lib/bindings/python/Cargo.toml --release --locked \
    --interpreter /opt/dynamo/build/bin/python --features "$runtime_features" --compatibility linux --auditwheel skip -o /out/wheels
features=(--features "$kvbm_features")
maturin build --manifest-path lib/bindings/kvbm/Cargo.toml --release --locked \
    --target-dir "${CARGO_TARGET_DIR}-kvbm" \
    --interpreter /opt/dynamo/build/bin/python --compatibility linux --auditwheel skip \
    "${features[@]}" -o /out/wheels
python -m pip wheel --no-deps --no-build-isolation -w /out/wheels . lib/gpu_memory_service
# The independent engine/physical path is separate from the Python KVBM entrypoint.
cargo build --release --locked -p kvbm-engine --example native_replicated \
    --no-default-features --features collectives --message-format=json > /out/native-build.jsonl
python container/platforms/audit.py native /out/native-build.jsonl /out/native
mkdir -p /out/vendor-patches
cp adapter/m05/patch_vendor.py adapter/m05/patch_ascend_tp.py /out/vendor-patches/
cp -a adapter/m05/patches /out/vendor-patches/
cp SOURCE.json /out/SOURCE.json
cp container/platforms/"$platform".json /out/profile.json
python -m pip freeze --all > /out/build-python.txt
dpkg-query -W > /out/build-os.txt
rustc --version > /out/rustc.txt
mkdir -p /out/locks
cp Cargo.lock /out/locks/workspace.Cargo.lock
cp lib/bindings/python/Cargo.lock /out/locks/runtime.Cargo.lock
cp lib/bindings/kvbm/Cargo.lock /out/locks/kvbm.Cargo.lock
cargo tree --locked --manifest-path lib/bindings/python/Cargo.toml --features "$runtime_features" -e features > /out/runtime-features.txt
cargo tree --locked --manifest-path lib/bindings/kvbm/Cargo.toml "${features[@]}" -e features > /out/kvbm-features.txt

