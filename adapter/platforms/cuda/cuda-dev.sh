#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
container="${CONTAINER:-dynamo-cuda-151-dev}"
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
image=nvcr.io/nvidia/ai-dynamo/vllm-runtime@sha256:2b224a823d52a198ec0eeb0e3e4f5cf26cd8a7fc2e9812ee16204b6b1e974870
case "${1:-}" in
    up)
        docker run -d --name "$container" --user 0 --init --gpus all \
            --network host --shm-size 16g \
            -v "$repo:/workspace/dynamo" -v /NFS_62:/NFS_62:ro \
            -v dynamo-cuda-151-cache:/var/cache/dynamo-cuda \
            -w /workspace/dynamo --entrypoint /bin/bash "$image" -lc 'exec sleep infinity'
        ;;
    bootstrap)
        actual="$(docker inspect --format '{{.Image}}' "$container")"
        [[ "$actual" == "sha256:2b224a823d52a198ec0eeb0e3e4f5cf26cd8a7fc2e9812ee16204b6b1e974870" ]] || {
            echo "Container is not the pinned Dynamo 1.5.1 image" >&2; exit 1;
        }
        docker exec -i -w /workspace/dynamo "$container" bash -se <<'SETUP'
apt-get update
apt-get install -y --no-install-recommends git g++
git config --global --add safe.directory /workspace/dynamo
python -m venv --system-site-packages /opt/dynamo/venv
/opt/dynamo/venv/bin/python -m pip install \
    'pytest==8.4.2' 'pytest-asyncio==1.2.0' 'pytest-timeout==2.4.0' \
    'pytest-benchmark==5.3.0' 'pytest-mock==3.16.0' 'wheel==0.45.1'
# The runtime image's OpenCV omits video backends; the CPU VP9 check needs one.
/opt/dynamo/venv/bin/python -m pip install --no-deps --ignore-installed \
    --report /var/cache/dynamo-cuda/opencv-install.json 'opencv-python-headless==5.0.0.93'
/opt/dynamo/venv/bin/python -m pip install --no-deps --no-build-isolation -e lib/gpu_memory_service
/opt/dynamo/venv/bin/python - <<'PY'
from pathlib import Path
import sysconfig

# Override Python sources while retaining the official Rust extension and engine stack.
# GMS's source root must precede the globally installed regular package as well.
paths = ["/workspace/dynamo/components/src", "/workspace/dynamo/lib/bindings/python/src", "/workspace/dynamo/lib"]
Path(sysconfig.get_path("purelib"), "dynamo_source.pth").write_text(
    "import sys; sys.path[:0] = " + repr(paths) + "\n"
)
PY
/opt/dynamo/venv/bin/python - <<'PY'
import importlib.metadata as m
import gpu_memory_service
from dynamo.common.platform.versions import validate_vllm_versions

validate_vllm_versions("cuda")
assert m.version("torch") == "2.13.0+cu130"
assert m.version("ai-dynamo-runtime") == "1.5.1"
assert gpu_memory_service.__file__.startswith("/workspace/dynamo/")
print({name: m.version(name) for name in ("vllm", "torch", "ai-dynamo-runtime")})
print("GMS source:", gpu_memory_service.__file__)
PY
SETUP
        ;;
    *) echo "Usage: bash adapter/platforms/cuda/cuda-dev.sh {up|bootstrap}" >&2; exit 2 ;;
esac
