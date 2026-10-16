#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Run inside the MetaX development container after installing Dynamo.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DYNAMO_HOME="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$DYNAMO_HOME/examples/common/gpu_utils.sh"
source "$DYNAMO_HOME/examples/common/launch_utils.sh"

source "${DYNAMO_VENV:-/opt/dynamo/venv}/bin/activate"
: "${MODEL_PATH:?Set MODEL_PATH to the downloaded model directory inside the container}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export DYN_NAMESPACE="${DYN_NAMESPACE:-metax-basic}"
export DYN_DISCOVERY_BACKEND=file
export DYN_REQUEST_PLANE=tcp
export DYN_EVENT_PLANE=zmq
export DYN_FILE_KV="${DYN_FILE_KV:-/tmp/dynamo-metax-basic}"
export DYN_HTTP_PORT="${DYN_HTTP_PORT:-8000}"
export DYN_SYSTEM_PORT="${DYN_SYSTEM_PORT:-8081}"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
MAX_CONCURRENT_SEQS="${MAX_CONCURRENT_SEQS:-4}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen3-0.6B}"

# Explicit per-worker KV cap; do not reserve most of a 64 GiB C500 for smoke tests.
: "${_PROFILE_OVERRIDE_VLLM_KV_CACHE_BYTES:=1073741824}"
GPU_MEM_ARGS=$(build_vllm_gpu_mem_args)
read -r -a GPU_MEM_ARGV <<< "$GPU_MEM_ARGS"

trap dynamo_exit_trap EXIT

print_launch_banner "MetaX basic text inference" "$SERVED_MODEL_NAME" "$DYN_HTTP_PORT"
DYN_PLATFORM=cpu python -m dynamo.frontend --router-mode round-robin &
DYN_PLATFORM=metax python -m dynamo.vllm --model "$MODEL_PATH" \
    --served-model-name "$SERVED_MODEL_NAME" \
    --enforce-eager --max-model-len "$MAX_MODEL_LEN" \
    --max-num-seqs "$MAX_CONCURRENT_SEQS" \
    "${GPU_MEM_ARGV[@]}" "$@" &
wait_any_exit
