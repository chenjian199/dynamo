#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Reuse the same assertions as MetaX. Run inside the Ascend development container.
set -euo pipefail
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "${DYNAMO_VENV:-/opt/dynamo/venv}/bin/activate"
MODE="${1:-help}"
if [[ $# -gt 0 ]]; then shift; fi
case "$MODE" in
    basic)
        python "$REPO_DIR/adapter/platforms/metax/metax-smoke.py" \
            --url http://127.0.0.1:8000 --worker-url http://127.0.0.1:8081 \
            --namespace ascend_basic "$@"
        ;;
    kv)
        python "$REPO_DIR/adapter/platforms/metax/metax-kv-smoke.py" --platform ascend \
            --namespace ascend-kv --discovery-dir /tmp/dynamo-ascend-kv --block-size 128 "$@"
        ;;
    platform)
        : "${MODEL_PATH:?Set MODEL_PATH to the Qwen3-0.6B model directory}"
        python "$REPO_DIR/adapter/m01/platform-smoke.py" --platform ascend \
            --namespace ascend-kv --discovery-dir /tmp/dynamo-ascend-kv \
            --model-path "$MODEL_PATH" "$@"
        ;;
    *) echo 'Usage: bash adapter/platforms/ascend/ascend-smoke.sh {basic|kv|platform} --output /tmp/result.json' ;;
esac
