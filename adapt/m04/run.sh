#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
platform=${1:?Usage: run.sh metax|ascend|cuda OUTPUT_DIRECTORY}
output=${2:?Provide a new output directory}
case "$platform" in metax|ascend|cuda) ;; *) echo 'Unsupported platform' >&2; exit 2;; esac
mkdir "$output"
output=$(cd "$output" && pwd)
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
cargo_bin=${CARGO_BIN:-/root/.cargo/bin/cargo}
export CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-/var/cache/dynamo-${platform}/m04-target}
if [[ $(uname -m) == aarch64 ]]; then
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS='-C target-cpu=generic -C force-frame-pointers=yes --cfg tokio_unstable'
fi
"$cargo_bin" test --locked -p dynamo-platform 2>&1 | tee "$output/contract.log"
"$cargo_bin" test --locked -p dynamo-memory --no-default-features --lib 2>&1 | tee "$output/cpu-memory.log"
"$cargo_bin" tree --locked -p dynamo-memory --no-default-features | tee "$output/cpu-dependencies.log"
"$cargo_bin" clippy --locked -p dynamo-platform -p dynamo-platform-runtime --all-targets -- -D warnings 2>&1 | tee "$output/clippy.log"
"$cargo_bin" run --locked -p dynamo-platform-runtime --example device_smoke -- "$platform" 2>&1 | tee "$output/native.log"
python3 - "$platform" "$output" <<'PY'
import hashlib, json, platform, subprocess, sys
from pathlib import Path
root = Path.cwd()
files = [Path('Cargo.toml'), Path('Cargo.lock')]
for base in ('lib/platform', 'lib/platform-runtime', 'lib/memory'):
    files.extend(p for p in Path(base).rglob('*') if p.suffix in ('.rs', '.toml'))
report = dict(platform=sys.argv[1], machine=platform.machine(), passed=True,
              source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)})
Path(sys.argv[2], 'source.json').write_text(json.dumps(report, indent=2)+'\n')
PY
