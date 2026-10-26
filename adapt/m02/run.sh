#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# Run on the target host, from a checkout, against a fresh platform-run container.
# CUDA / Ascend: container-local IDs; MetaX: physical IDs (e.g. 1,2).
platform=${1:?Usage: run.sh cuda|metax|ascend CONTAINER DEVICES BUILD_DIR OUTPUT_DIR}
container=${2:?container}
devices=${3:?two devices}
build=${4:?build directory}
output=${5:?new evidence directory}
case "$platform" in cuda|metax|ascend) ;; *) exit 2 ;; esac
root=$(cd "$(dirname "$0")/../.." && pwd)
bundle=$(mktemp -d /var/tmp/dynamo-m02-tests.XXXXXX)
trap 'rm -rf "$bundle"' EXIT
mkdir -p "$bundle/adapt/m05" "$bundle/adapt/m06" "$bundle/tests/utils" "$bundle/gms"
cp "$root"/container/platforms/{smoke,native-smoke,cpu-smoke}.py "$bundle/"
cp "$root"/adapt/m05/{run.py,patch_vendor.py,patch_ascend_tp.py} "$bundle/adapt/m05/"
cp -a "$root/adapt/m05/patches" "$bundle/adapt/m05/"
cp "$root"/adapt/m06/{run.py,sources.txt} "$bundle/adapt/m06/"
cp "$root/tests/__init__.py" "$bundle/tests/"
cp "$root"/tests/utils/*.py "$bundle/tests/utils/"
cp "$root/lib/gpu_memory_service/tests/test_platform_vmm.py" "$bundle/gms/"
docker exec "$container" test ! -e /test
docker cp "$bundle" "$container:/test"
exec_test() {
    docker exec "$container" /opt/dynamo/bin/entrypoint.sh python "$@"
}
exec_test /test/cpu-smoke.py > "$bundle/cpu.json"
exec_test /test/smoke.py --platform "$platform" --devices "$devices"
exec_test /test/native-smoke.py --platform "$platform" --devices "$devices" --output /tmp/m02-native
model=/models/Qwen_Series/Qwen3-0.6B
common=(/test/adapt/m06/run.py --platform "$platform" --devices "${devices%%,*}" --model "$model")
exec_test "${common[@]}" --baseline --output /tmp/m02-kvbm-baseline
native=()
if [[ $platform == cuda ]]; then native=(--native-cuda); fi
exec_test "${common[@]}" "${native[@]}" --disk --baseline-summary /tmp/m02-kvbm-baseline/summary.json --output /tmp/m02-kvbm-disk
if [[ $platform == cuda ]]; then
    exec_test "${common[@]}" --baseline-summary /tmp/m02-kvbm-baseline/summary.json --output /tmp/m02-kvbm-nixl-host
fi
exec_test /test/adapt/m05/run.py --platform "$platform" --devices "$devices" --model "$model" --output /tmp/m02-pd
if [[ $platform != ascend ]]; then
    visibility=(-e "CUDA_VISIBLE_DEVICES=${devices%%,*}")
    if [[ $platform == metax ]]; then visibility+=(-e "MACA_VISIBLE_DEVICES=${devices%%,*}"); fi
    docker exec "${visibility[@]}" -e M07_DEVICE=0 -e "M07_MODEL=$model" "$container" \
        /opt/dynamo/bin/entrypoint.sh timeout 900 python -m pytest -c /dev/null --noconftest \
        /test/gms/test_platform_vmm.py -q --junitxml=/tmp/m02-gms.xml
fi
python3 "$root/adapt/m02/collect.py" --build "$build" --container "$container" \
    --output "$output" --cpu-report "$bundle/cpu.json"
