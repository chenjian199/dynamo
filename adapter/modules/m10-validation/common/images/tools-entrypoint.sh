#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
export PATH=/opt/dynamo/tools-venv/bin:$PATH
export LD_LIBRARY_PATH=/opt/dynamo/native:${LD_LIBRARY_PATH:-}
exec "$@"
