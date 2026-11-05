#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Requires an ffmpeg build with the software VP9 encoder (CUDA dev image has it).
set -euo pipefail
output="${1:-$(dirname -- "${BASH_SOURCE[0]}")/red.webm}"
ffmpeg -nostdin -v error -f lavfi -i color=c=red:s=64x64:r=4:d=1 \
    -c:v libvpx-vp9 -fflags +bitexact -flags:v +bitexact -map_metadata -1 "$output"
