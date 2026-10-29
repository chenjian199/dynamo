#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
platform=${1:?Usage: run-suite.sh cuda|metax|ascend OUTPUT}
output=${2:?Specify a fresh result directory}
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
case "$platform" in
  ascend) python=(bash adapter/m05/ascend-python.sh) ;;
  metax) python=("${PYTHON:-/opt/dynamo/venv/bin/python}") ;;
  cuda) python=("${PYTHON:-/opt/dynamo/m06-venv/bin/python}") ;;
  *) exit 2 ;;
esac
mkdir -p "$output"
run() {
    local name=$1
    shift
    "${python[@]}" adapter/m06/run.py --platform "$platform" \
        --output "$output/$name" --devices "${DEVICE:-0}" \
        --model "${MODEL:-/NFS_62/Models/Qwen_Series/Qwen3-0.6B}" \
        "$@" > "$output/$name.runner.txt" 2>&1
}
run baseline --baseline
run host --cycles "${CYCLES:-4}" --cancel --baseline-summary "$output/baseline/summary.json"
# Use the explicitly selected portable path on CUDA to compare all three SDKs.
native=()
if [[ $platform == cuda ]]; then native=(--native-cuda); fi
run disk --disk "${native[@]}" --baseline-summary "$output/baseline/summary.json"
run direct-disk --direct-disk "${native[@]}" --baseline-summary "$output/baseline/summary.json"
tp_args=()
if [[ ${DISABLE_CUSTOM_ALL_REDUCE:-0} == 1 ]]; then
    tp_args=(--disable-custom-all-reduce)
fi
run baseline-tp2 --baseline --tp 2 --devices "${TP_DEVICES:-0,1}" "${tp_args[@]}"
run tp2 --tp 2 --devices "${TP_DEVICES:-0,1}" --cancel \
    --baseline-summary "$output/baseline-tp2/summary.json" "${tp_args[@]}"
run small-host --host-blocks 1 --allow-cache-miss --cancel "${native[@]}"
