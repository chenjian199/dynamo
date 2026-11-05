#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run on the platform's native build host after the runtime build succeeds.
set -euo pipefail
platform=${1:?Usage: build-derived.sh PLATFORM WORKDIR RELEASE_TAG}
work=${2:?work directory containing source and tools context}
tag=${3:?release tag}
runtime="heyi/dynamo-vllm-$platform-runtime:$tag"
proxy_args=()
if [[ -n ${BUILD_HTTP_PROXY:-} ]]; then
    export HTTP_PROXY="$BUILD_HTTP_PROXY" HTTPS_PROXY="$BUILD_HTTP_PROXY"
    proxy_args=(--build-arg HTTP_PROXY --build-arg HTTPS_PROXY)
fi
python3 - "${RUNTIME_RECORD:-$work/$platform-runtime/image.json}" "$runtime" <<'PY'
import json, subprocess, sys
record = json.load(open(sys.argv[1]))
actual = json.loads(subprocess.check_output(['docker', 'image', 'inspect', sys.argv[2]]))[0]
assert record['id'] == actual['Id'], 'Runtime tag changed after the build'
PY
python3 "$work/source/container/platform-build.py" --device "$platform" --target dev \
    --runtime-image "$runtime" --tag "heyi/dynamo-vllm-$platform-dev:$tag" \
    --output "$work/$platform-dev" --rust-mirror rsproxy > "$work/$platform-dev.log" 2>&1 &
dev_pid=$!
tools_status=0
docker build --network host "${proxy_args[@]}" --build-arg "RUNTIME_IMAGE=$runtime" --build-arg "PLATFORM=$platform" \
    -f "$work/$platform-tools-context/tools.Dockerfile" \
    -t "heyi/dynamo-tools-$platform:$tag" "$work/$platform-tools-context" \
    > "$work/$platform-tools.log" 2>&1 || tools_status=$?
dev_status=0
wait "$dev_pid" || dev_status=$?
printf 'dev=%s tools=%s\n' "$dev_status" "$tools_status" | tee "$work/$platform-derived-status.txt"
test "$dev_status" -eq 0 && test "$tools_status" -eq 0
