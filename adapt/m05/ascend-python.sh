#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# CANN 9.1 / Mooncake 0.3.11.post1: resolve CANN symbols before any Python
# plugin imports. Use inside the validated Ascend 0.23.0 development image.
CANN_DATADIST=/usr/local/Ascend/cann-9.1.0/lib64/libllm_datadist.so
if [[ ! -r "$CANN_DATADIST" ]]; then
    echo "Missing validated CANN library: $CANN_DATADIST" >&2
    exit 1
fi
# Mooncake loads its Go etcd helper even with P2PHANDSHAKE. Bound that
# auxiliary runtime rather than creating workers for every host CPU.
# Keep the original environment area intact when vLLM sets process titles;
# a later-loaded Go shared library reads that area during initialization.
export SPT_NOENV="${SPT_NOENV:-1}"
export GOMAXPROCS="${GOMAXPROCS:-2}"
# Bound glibc arena retention in the many-threaded development worker.
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
export LD_PRELOAD="$CANN_DATADIST${LD_PRELOAD:+:$LD_PRELOAD}"
exec /opt/dynamo/venv/bin/python "$@"
