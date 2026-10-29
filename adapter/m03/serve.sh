#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Dedicated manual certification process group; not a shared production service.
set -euo pipefail
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$REPO_DIR/examples/common/gpu_utils.sh"
source "$REPO_DIR/examples/common/launch_utils.sh"
source "${DYNAMO_VENV:-/opt/dynamo/venv}/bin/activate"
: "${PLATFORM:?Set PLATFORM to metax, ascend or cuda}"
: "${MODEL_PATH:?Set MODEL_PATH to model weights}"
TP="${TP:-1}"
DP="${DP:-1}"
case "$PLATFORM" in
  metax|cuda) export CUDA_VISIBLE_DEVICES="${DEVICES:-0,1}"; BLOCK_SIZE="${BLOCK_SIZE:-64}" ;;
  ascend) export ASCEND_RT_VISIBLE_DEVICES="${DEVICES:-0,1}"; BLOCK_SIZE="${BLOCK_SIZE:-128}" ;;
  *) echo "Unsupported platform: $PLATFORM" >&2; exit 2 ;;
esac
export DYN_NAMESPACE="${DYN_NAMESPACE:-m03-${PLATFORM}}"
export DYN_DISCOVERY_BACKEND=file DYN_REQUEST_PLANE=tcp DYN_EVENT_PLANE=zmq
export DYN_FILE_KV="${DYN_FILE_KV:-/tmp/dynamo-${DYN_NAMESPACE}}"
export DYN_HTTP_PORT="${DYN_HTTP_PORT:-8010}" DYN_SYSTEM_PORT="${DYN_SYSTEM_PORT:-8090}"
export DYN_ROUTER_USE_KV_EVENTS=true PYTHONHASHSEED=0 HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
: "${_PROFILE_OVERRIDE_VLLM_KV_CACHE_BYTES:=1073741824}"
GPU_MEM_ARGS=$(build_vllm_gpu_mem_args)
read -r -a GPU_MEM_ARGV <<< "$GPU_MEM_ARGS"
FRONTEND_PID=""
WORKER_PID=""
cleanup() {
  trap - EXIT TERM INT
  # Let Dynamo stop its engine and producers before any process-group fallback.
  for pid in "$WORKER_PID" "$FRONTEND_PID"; do
    if [[ -n "$pid" ]]; then kill -TERM "$pid" 2>/dev/null || true; fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT
print_launch_banner "M03 $PLATFORM TP=$TP DP=$DP" "${SERVED_MODEL_NAME:-m03-model}" "$DYN_HTTP_PORT"
DYN_PLATFORM=cpu python -m dynamo.frontend --router-mode kv &
FRONTEND_PID=$!
DYN_PLATFORM="$PLATFORM" DYN_FORWARDPASS_METRIC_PORT="${FPM_PORT:-20094}" \
  python -m dynamo.vllm --model "$MODEL_PATH" --served-model-name "${SERVED_MODEL_NAME:-m03-model}" \
    --enforce-eager --max-model-len "${MAX_MODEL_LEN:-4096}" --max-num-seqs 4 \
    --tensor-parallel-size "$TP" --data-parallel-size "$DP" --data-parallel-size-local "$DP" \
    --enable-prefix-caching --block-size "$BLOCK_SIZE" \
    --kv-events-config "{\"publisher\":\"zmq\",\"topic\":\"kv-events\",\"endpoint\":\"tcp://*:${KV_PORT:-20092}\",\"enable_kv_cache_events\":true}" \
    "${GPU_MEM_ARGV[@]}" "$@" &
WORKER_PID=$!
wait -n
