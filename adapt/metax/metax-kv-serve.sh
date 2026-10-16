#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Run in the development container, in its own process group (setsid --wait).
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DYNAMO_HOME="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$DYNAMO_HOME/examples/common/gpu_utils.sh"
source "$DYNAMO_HOME/examples/common/launch_utils.sh"
source "${DYNAMO_VENV:-/opt/dynamo/venv}/bin/activate"
: "${MODEL_PATH:?Set MODEL_PATH to the model directory inside the container}"

export DYN_NAMESPACE="${DYN_NAMESPACE:-metax-kv}"
export DYN_DISCOVERY_BACKEND=file DYN_REQUEST_PLANE=tcp DYN_EVENT_PLANE=zmq
export DYN_FILE_KV="${DYN_FILE_KV:-/tmp/dynamo-metax-kv}"
export DYN_HTTP_PORT="${DYN_HTTP_PORT:-8001}"
export DYN_ROUTER_USE_KV_EVENTS=true
export PYTHONHASHSEED=0 HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen3-0.6B}"
BLOCK_SIZE="${BLOCK_SIZE:-64}"
: "${_PROFILE_OVERRIDE_VLLM_KV_CACHE_BYTES:=1073741824}"
GPU_MEM_ARGS=$(build_vllm_gpu_mem_args)
read -r -a GPU_MEM_ARGV <<< "$GPU_MEM_ARGS"
trap dynamo_exit_trap EXIT

print_launch_banner "MetaX KV-aware routing (two TP=1 workers)" "$SERVED_MODEL_NAME" "$DYN_HTTP_PORT"
DYN_PLATFORM=cpu python -m dynamo.frontend --router-mode kv &
for worker in 1 2; do
    if [[ "$worker" == 1 ]]; then
        gpu="${WORKER_GPU1:-1}"
        system_port="${DYN_SYSTEM_PORT1:-8082}"
        event_port="${KV_EVENT_PORT1:-20082}"
        fpm_port="${FPM_PORT1:-20084}"
    else
        gpu="${WORKER_GPU2:-2}"
        system_port="${DYN_SYSTEM_PORT2:-8083}"
        event_port="${KV_EVENT_PORT2:-20083}"
        fpm_port="${FPM_PORT2:-20085}"
    fi
    # Enables InstrumentedScheduler, including immediate reset-event publication.
    CUDA_VISIBLE_DEVICES="$gpu" DYN_SYSTEM_PORT="$system_port" \
        DYN_PLATFORM=metax DYN_FORWARDPASS_METRIC_PORT="$fpm_port" \
        python -m dynamo.vllm --model "$MODEL_PATH" \
        --served-model-name "$SERVED_MODEL_NAME" \
        --enforce-eager --enable-prefix-caching --block-size "$BLOCK_SIZE" \
        --max-model-len "${MAX_MODEL_LEN:-4096}" \
        --max-num-seqs "${MAX_CONCURRENT_SEQS:-4}" \
        --kv-events-config "{\"publisher\":\"zmq\",\"topic\":\"kv-events\",\"endpoint\":\"tcp://*:$event_port\",\"enable_kv_cache_events\":true}" \
        "${GPU_MEM_ARGV[@]}" "$@" &
done
wait_any_exit
