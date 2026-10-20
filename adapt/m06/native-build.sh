#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
platform=${1:?Usage: native-build.sh cuda|metax|ascend}
case "$platform" in cuda|metax|ascend) ;; *) exit 2 ;; esac
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
export PATH="/root/.cargo/bin:$PATH"
export CUDARC_CUDA_VERSION=13000
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-/var/cache/dynamo-${platform}/m06-independent-target}"
export CARGO_PROFILE_DEV_DEBUG=0
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-16}"
if [[ $(uname -m) == aarch64 ]]; then
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS='-C target-cpu=generic -C force-frame-pointers=yes --cfg tokio_unstable'
fi
mkdir -p "$CARGO_TARGET_DIR/native-bin"
args=(--locked)
if [[ ${ONLINE:-0} != 1 ]]; then args+=(--offline); fi
cargo build -p kvbm-engine --example native_replicated --no-default-features \
    --features collectives "${args[@]}" --message-format=json > "$CARGO_TARGET_DIR/native-build.jsonl"
python3 - "$CARGO_TARGET_DIR" <<'PY'
import hashlib
import json
import shutil
import sys
from pathlib import Path

target = Path(sys.argv[1])
out = target / 'native-bin'
binary = library = None
for line in (target / 'native-build.jsonl').read_text().splitlines():
    item = json.loads(line)
    if item.get('reason') == 'compiler-artifact' and item['target']['name'] == 'native_replicated':
        binary = Path(item['executable'])
    if item.get('reason') == 'build-script-executed' and 'kvbm-kernels' in item['package_id']:
        library = Path(item['out_dir']) / 'libkvbm_kernels.so'
assert binary and binary.is_file() and library and library.is_file(), 'missing native build artifacts'
for path in (binary, library):
    shutil.copy2(path, out / path.name)
files = Path('adapt/m06/sources.txt').read_text().splitlines()
record = {'files': {f: hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},
          'artifacts': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (binary, library)}}
(out / 'build-source.json').write_text(json.dumps(record, indent=2) + '\n')
print(out)
PY
