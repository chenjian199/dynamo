#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Use the release source and dev image; no accelerator or network is needed.
set -euo pipefail
cd /workspace/dynamo
export CARGO_NET_OFFLINE=true CARGO_PROFILE_DEV_DEBUG=0
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-16}
export CARGO_TARGET_DIR=/build-cache/m10-contract-target
if [[ $(uname -m) == aarch64 ]]; then
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS='-C target-cpu=generic -C force-frame-pointers=yes --cfg tokio_unstable'
fi
cargo test --locked -p dynamo-platform
cargo test --locked -p dynamo-memory --no-default-features --lib
cargo clippy --locked -p dynamo-platform -p dynamo-platform-runtime --all-targets -- -D warnings
cargo build --locked -p dynamo-platform-runtime --example device_smoke
echo M10_RUST_CONTRACTS_PASSED
