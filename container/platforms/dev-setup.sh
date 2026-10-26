#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# Keep the Ubuntu suites and signing keys, using the upstream HTTPS mirror for
# MetaX build tools; the inherited Aliyun endpoint is slow on this network.
if [[ $DYNAMO_IMAGE_PLATFORM == metax ]]; then
    sed -i -e 's|http://mirrors.aliyun.com/ubuntu/|https://archive.ubuntu.com/ubuntu/|g' \
        -e 's|https://mirrors.aliyun.com/ubuntu/|https://archive.ubuntu.com/ubuntu/|g' /etc/apt/sources.list
fi
python /opt/dynamo/bin/audit.py snapshot /opt/dynamo/dev/runtime.json
python - <<'PY'
import json
from pathlib import Path
x=json.loads(Path('/opt/dynamo/dev/runtime.json').read_text())
Path('/opt/dynamo/dev/runtime-constraints.txt').write_text(''.join(f'{k}=={v}\n' for k,v in sorted(x['packages'].items())))
PY
apt-get update
apt-get install -y --no-install-recommends build-essential pkg-config cmake protobuf-compiler \
    libprotobuf-dev libssl-dev libclang-dev libudev-dev libnuma-dev libzmq3-dev \
    git curl ca-certificates patch gdb strace
rm -rf /var/lib/apt/lists/*
python -m pip install -c /opt/dynamo/dev/runtime-constraints.txt \
    -r /opt/dynamo/dev/dev.txt --report /opt/dynamo/dev/python-install.json
if [[ ${RUST_MIRROR:-} == rsproxy ]]; then
    export RUSTUP_DIST_SERVER=https://rsproxy.cn RUSTUP_UPDATE_ROOT=https://rsproxy.cn/rustup
fi
curl --fail --location --retry 3 https://sh.rustup.rs -o /tmp/dynamo-rustup.sh
sh /tmp/dynamo-rustup.sh -y --no-modify-path --profile minimal --default-toolchain none
rust_version=$(python -c 'import tomllib; print(tomllib.load(open("/opt/dynamo/dev/rust-toolchain.toml", "rb"))["toolchain"]["channel"])')
rustup toolchain install "$rust_version" --profile minimal --component rustfmt --component clippy
rustup default "$rust_version"
mkdir -p /opt/dynamo/dev/downloads
swagger=/opt/dynamo/dev/downloads/swagger-ui-v5.17.14.zip
curl --http1.1 --fail --location --retry 3 --max-time 120 \
    https://codeload.github.com/swagger-api/swagger-ui/zip/refs/tags/v5.17.14 -o "$swagger"
printf '%s  %s\n' 481244d0812097b11fbaeef79f71d942b171617f9c9f9514e63acbe13e71ccdc "$swagger" | sha256sum -c -
python /opt/dynamo/bin/dev-check.py
rustc --version > /opt/dynamo/dev/rustc.txt
python -m pip freeze --all > /opt/dynamo/dev/python.txt
dpkg-query -W > /opt/dynamo/dev/os.txt
