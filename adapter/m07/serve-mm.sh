#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Run under adapter/m03/run-case.py, which owns and bounds this process group.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${DYNAMO_VENV:-/opt/dynamo/venv}/bin/activate"
: "${PLATFORM:?}"
: "${MODEL_PATH:?}"
: "${DEVICES:?}"
export DYN_NAMESPACE="m07-${PLATFORM}" DYN_DISCOVERY_BACKEND=file
export DYN_REQUEST_PLANE=tcp DYN_EVENT_PLANE=zmq
export DYN_FILE_KV="/tmp/dynamo-${DYN_NAMESPACE}"
export HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export DYN_VLLM_ENCODER_MAX_NUM_SEQS=2
export DYN_VLLM_SKIP_ENCODER_ONLY_KERNEL_WARMUP=1
case "$PLATFORM" in
    metax) export CUDA_VISIBLE_DEVICES="$DEVICES" MACA_VISIBLE_DEVICES="$DEVICES" ;;
    cuda) export CUDA_VISIBLE_DEVICES="$DEVICES" ;;
    ascend) export ASCEND_RT_VISIBLE_DEVICES="$DEVICES" ;;
    *) exit 2 ;;
esac
encoder="" serving=""
cleanup() {
    trap - EXIT TERM INT
    for pid in "$encoder" "$serving"; do
        if [[ -n "$pid" ]]; then kill -TERM "$pid" 2>/dev/null || true; fi
    done
    wait || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT
CUDA_VISIBLE_DEVICES="${ENCODER_DEVICES:-$DEVICES}" \
MACA_VISIBLE_DEVICES="${ENCODER_DEVICES:-$DEVICES}" \
ASCEND_RT_VISIBLE_DEVICES="${ENCODER_DEVICES:-$DEVICES}" \
DYN_PLATFORM="$PLATFORM" DYN_SYSTEM_PORT=8091 DYN_FORWARDPASS_METRIC_PORT=20096 \
python -m dynamo.vllm --model "$MODEL_PATH" --served-model-name m03-model \
    --enable-multimodal --disaggregation-mode encode --enforce-eager \
    --embedding-transfer-mode local --max-model-len 4096 \
    --limit-mm-per-prompt '{"image":1,"video":0}' \
    --mm-processor-kwargs '{"max_pixels":200704}' \
    --multimodal-embedding-cache-capacity-gb 0.01 &
encoder=$!
bash "$root/adapter/m03/serve.sh" --route-to-encoder --enable-mm-embeds \
    --embedding-transfer-mode local "$@" &
serving=$!
wait -n
