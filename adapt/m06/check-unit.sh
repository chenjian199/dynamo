#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
platform=${1:?Usage: check-unit.sh cuda|metax|ascend}
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# Individual platform tests own this setting. A global value changes legacy
# mock-engine tests into explicit-platform startup tests with incomplete configs.
unset DYN_PLATFORM
case "$platform" in
  ascend)
    export VLLM_DISABLE_SHARED_EXPERTS_STREAM=1
    python=(bash adapt/m05/ascend-python.sh)
    ;;
  metax) python=("${PYTHON:-/opt/dynamo/venv/bin/python}") ;;
  cuda) python=("${PYTHON:-/opt/dynamo/m06-venv/bin/python}") ;;
  *) exit 2 ;;
esac
"${python[@]}" -m pytest -q -W 'ignore:fla package is not installed:UserWarning' \
    components/src/dynamo/common/platform/tests \
    components/src/dynamo/vllm/tests/test_vllm_unit.py \
    components/src/dynamo/vllm/tests/test_vllm_instrumented_scheduler.py \
    components/src/dynamo/vllm/tests/test_vllm_worker_factory.py \
    components/src/dynamo/vllm/tests/test_vllm_publisher.py \
    components/src/dynamo/vllm/tests/test_vllm_kv_events_api.py \
    components/src/dynamo/vllm/tests/test_vllm_errors.py \
    components/src/dynamo/vllm/tests/test_platform_integration.py \
    components/src/dynamo/vllm/tests/test_pd_contract.py \
    components/src/dynamo/vllm/tests/test_vllm_kv_connector_protocols.py \
    tests/kvbm_integration/test_platform_events.py \
    tests/kvbm_integration/test_pd_metadata.py
