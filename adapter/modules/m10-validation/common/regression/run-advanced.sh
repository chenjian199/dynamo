#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run in a dedicated two-device final-runtime Job with tests-only /harness.
set -euo pipefail
platform=${1:?cuda or ascend}
out=${2:?new output directory}
export PLATFORM=$platform MODEL_PATH=/models/Qwen3-0.6B DEVICES=0,1
# Read identity from the immutable runtime artifact; never reuse a prior release SHA.
observed_commit=$(python -c 'import json; print(json.load(open("/opt/dynamo/artifacts/SOURCE.json"))["commit"])')
if [[ -n ${DYNAMO_RELEASE_SOURCE_COMMIT:-} && $DYNAMO_RELEASE_SOURCE_COMMIT != "$observed_commit" ]]; then
    echo "Requested source commit differs from the installed runtime" >&2
    exit 2
fi
export DYNAMO_RELEASE_SOURCE_COMMIT=$observed_commit
export PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1
export PYTHONPATH=/harness/test-deps${PYTHONPATH:+:$PYTHONPATH}
cd /harness
mkdir -p "$out"
python check-installed.py --role runtime > "$out/installed.json"
python adapter/modules/m03-engine-integration/common/engine/create-lora.py --model "$MODEL_PATH" --output "/var/cache/dynamo-$platform/models/m03-lora"
failures=0
run_case() {
 local name=$1
 shift
 if "$@" > "$out/$name.console.log" 2>&1; then
  printf '%s\tpassed\n' "$name" >> "$out/status.tsv"
 else
  printf '%s\tfailed\n' "$name" >> "$out/status.tsv"
  failures=$((failures+1))
 fi
}
run_case lora-dp2 env DYN_LORA_ENABLED=true TP=1 DP=2 python adapter/modules/m03-engine-integration/common/engine/run-case.py \
 --output "$out/lora-dp2" --check lora -- --enable-lora --max-loras 2 --max-lora-rank 8
run_case ngram env TP=1 DP=1 DEVICES=0 python adapter/modules/m03-engine-integration/common/engine/run-case.py \
 --output "$out/ngram" --check spec -- \
 --speculative-config '{"method":"ngram","num_speculative_tokens":3,"prompt_lookup_min":2,"prompt_lookup_max":4}'
if [[ $platform == cuda ]]; then
 quantized=/var/cache/dynamo-cuda/models/Qwen3-0.6B-M10-W8A8
 python adapter/modules/m03-engine-integration/common/engine/create-quantized.py --model "$MODEL_PATH" --output "$quantized"
 run_case int8 env TP=1 DP=1 DEVICES=0 MODEL_PATH="$quantized" python adapter/modules/m03-engine-integration/common/engine/run-case.py \
 --output "$out/int8" -- --quantization compressed-tensors
fi
cat "$out/status.tsv"
[[ $failures == 0 ]]
