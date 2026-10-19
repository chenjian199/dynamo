#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# Experimental UCX endpoint cleanup, isolated from the image's installed NIXL.
# Run inside the validated MetaX 0.26.0 image. Never overwrite its bundled plugin.
BUILD_ROOT=${1:?Usage: build-metax-nixl.sh NEW_ABSOLUTE_BUILD_DIRECTORY}
[[ "$BUILD_ROOT" == /* ]] || { echo 'Use an absolute build directory' >&2; exit 1; }
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PYTHON=/opt/dynamo/venv/bin/python
SITE=$($PYTHON - <<'PY'
import hashlib
import importlib.metadata
import importlib.util
from pathlib import Path
if importlib.metadata.version('nixl') != '1.0.0+maca3.8.2.208':
    raise SystemExit('This build only supports NIXL 1.0.0+maca3.8.2.208')
site = Path(importlib.util.find_spec('nixl').origin).parent.parent
expected = {
    '.nixl.mesonpy.libs/plugins/libplugin_UCX.so': '7625e8e466bf0ee682f9a334821aba4f63f16b23c4dca511405e4a69234375aa',
    'nixl.libs/libucp-4087c85a.so.0.0.0': '1c3523c2c018f383c5471f42a52f634f6f2b0bc222cf07a67c3b3377af50005a',
    'nixl.libs/libucs-1e546160.so.0.0.0': '096a6c98f21dd4df577c991fb6c17aaa2135d711cfc44a995693004834a1cf40',
}
for name, sha in expected.items():
    if hashlib.sha256((site / name).read_bytes()).hexdigest() != sha:
        raise SystemExit(f'Unvalidated binary: {site / name}')
print(site)
PY
)
mkdir -- "$BUILD_ROOT"
# Build dependencies only; the active vLLM/torch/MACA stack is not reinstalled.
apt-get update -qq
apt-get install -y -qq build-essential cmake git libasio-dev

git clone --depth 1 --branch v1.0.0 https://github.com/ai-dynamo/nixl.git "$BUILD_ROOT/nixl"
git clone --depth 1 --branch 20250814.1 https://github.com/abseil/abseil-cpp.git "$BUILD_ROOT/abseil"
[[ $(git -C "$BUILD_ROOT/nixl" rev-parse HEAD) == 4071a5329915b411373e1dac80a3e92ce15f0fa8 ]]
[[ $(git -C "$BUILD_ROOT/abseil" rev-parse HEAD) == d38452e1ee03523a208362186fd42248ff2609f6 ]]
git -C "$BUILD_ROOT/nixl" apply --unidiff-zero "$SCRIPT_DIR/patches/metax-nixl-endpoint.patch"
cmake -S "$BUILD_ROOT/abseil" -B "$BUILD_ROOT/abseil-build" \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_STANDARD=17 -DABSL_BUILD_TESTING=OFF
cmake --build "$BUILD_ROOT/abseil-build" -j "${BUILD_JOBS:-8}"
mapfile -d '' ABSL_LIBS < <(find "$BUILD_ROOT/abseil-build" -name '*.a' -print0)
mkdir "$BUILD_ROOT/plugins"
cd "$BUILD_ROOT/nixl"
g++ -std=c++17 -O2 -fPIC -shared -pthread \
  -Isrc/api/cpp -Isrc/api/cpp/backend -Isrc/infra -Isrc/core -Isrc/core/telemetry \
  -Isrc/utils -I"$BUILD_ROOT/abseil" -I/opt/maca/ucx/include \
  src/plugins/ucx/{config,mem_list,rkey,ucx_backend,ucx_plugin,ucx_utils}.cpp \
  -L"$SITE/.nixl.mesonpy.libs" -lnixl_common -lserdes \
  -Wl,--start-group "${ABSL_LIBS[@]}" -Wl,--end-group \
  "$SITE/nixl.libs/libucp-4087c85a.so.0.0.0" "$SITE/nixl.libs/libucs-1e546160.so.0.0.0" \
  -Wl,-rpath,"$SITE/.nixl.mesonpy.libs:$SITE/nixl.libs" \
  -o "$BUILD_ROOT/plugins/libplugin_UCX.so"
sha256sum "$BUILD_ROOT/plugins/libplugin_UCX.so" > "$BUILD_ROOT/plugin.sha256"
printf 'Built isolated plugin at %s/plugins; opt-in requires ucx_ep_close_force=yes.\n' "$BUILD_ROOT"
printf 'The UCX 1.14 pending-peer failure path is still NOT fixed.\n'
