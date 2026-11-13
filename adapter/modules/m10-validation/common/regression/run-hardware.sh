#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run inside an isolated two-device container; /harness contains tests only.
set -euo pipefail
platform=${1:?Usage: run-hardware.sh PLATFORM OUTPUT MODEL}
output=${2:?new result directory}
model=${3:?model path}
mkdir -p "$output"
cd /harness
export HF_HUB_OFFLINE=1 M07_DEVICE=0 PYTHONUNBUFFERED=1
export M07_MODEL=$model
python check-installed.py --role runtime > "$output/installed.json"
export PYTHONPATH=/harness/test-deps${PYTHONPATH:+:$PYTHONPATH}
python check-installed.py --role runtime > "$output/installed-with-test-plugins.json"
DYN_PLATFORM=cpu TORCH_DEVICE_BACKEND_AUTOLOAD=0 python container/platforms/cpu-smoke.py > "$output/cpu.json"
export DYN_PLATFORM=$platform
failures=0
run_case() {
    local name=$1
    shift
    echo "Starting $name"
    if "$@" > "$output/$name.console.log" 2>&1; then
        printf '%s\tpassed\n' "$name" >> "$output/status.tsv"
    else
        printf '%s\tfailed\n' "$name" >> "$output/status.tsv"
        failures=$((failures + 1))
    fi
}
run_case native python container/platforms/native-smoke.py --platform "$platform" --devices 0,1 --output "$output/native"
run_case inference python container/platforms/smoke.py --platform "$platform" --devices 0,1 --model "$model" --output "$output/inference"
run_case kvbm-baseline python adapter/modules/m06-kvbm-collectives/common/cache/run.py --platform "$platform" --devices 0,1 --tp 2 --model "$model" --disable-custom-all-reduce --baseline --output "$output/kvbm-baseline"
if python -c 'import json,sys; assert json.load(open(sys.argv[1]))["status"] == "passed"' "$output/kvbm-baseline/summary.json"; then
    run_case kvbm-host python adapter/modules/m06-kvbm-collectives/common/cache/run.py --platform "$platform" --devices 0,1 --tp 2 --model "$model" --disable-custom-all-reduce --cancel --cycles 2 --baseline-summary "$output/kvbm-baseline/summary.json" --output "$output/kvbm-host"
    disk_options=()
    if [[ $platform == cuda ]]; then disk_options+=(--native-cuda); fi
    run_case kvbm-disk python adapter/modules/m06-kvbm-collectives/common/cache/run.py --platform "$platform" --devices 0,1 --tp 2 --model "$model" --disable-custom-all-reduce --disk "${disk_options[@]}" --cancel --cycles 2 --baseline-summary "$output/kvbm-baseline/summary.json" --output "$output/kvbm-disk"
else
    printf 'kvbm-host\tblocked-by-baseline\nkvbm-disk\tblocked-by-baseline\n' >> "$output/status.tsv"
fi
if [[ $platform == ascend ]]; then
    run_case media python -m pytest --noconftest -c pytest.ini -v adapter/modules/m07-gms-multimodal/common/media/test_multimodal.py -k 'not nixl_read and not nixl_write and not write_late' --junitxml="$output/media.xml"
else
    media_options=()
    # The official CUDA base has no CPU video codecs; certify that test in
    # the separately built optional media image, not in the base runtime.
    if [[ $platform == cuda ]]; then media_options=(-k "not cpu_video_decode"); fi
    run_case media-gms python -m pytest --noconftest -c pytest.ini -v adapter/modules/m07-gms-multimodal/common/media/test_multimodal.py lib/gpu_memory_service/tests/test_platform_vmm.py "${media_options[@]}" --junitxml="$output/media-gms.xml"
fi
cat "$output/status.tsv"
test "$failures" -eq 0
