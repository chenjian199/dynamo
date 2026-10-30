#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
cd "$(dirname "$0")/../.."
export PYTHONPATH="$PWD/components/src${PYTHONPATH:+:$PYTHONPATH}"
python -m pytest -c adapter/m09/pytest.ini -q \
  components/src/dynamo/common/platform/tests \
  components/src/dynamo/planner/tests/unit/test_platform_resources.py \
  components/src/dynamo/planner/tests/unit/test_kubernetes_connector.py \
  components/src/dynamo/planner/tests/unit/test_mdc.py \
  components/src/dynamo/planner/tests/unit/test_kube.py \
  components/src/dynamo/planner/tests/unit/test_environment.py \
  components/src/dynamo/planner/tests/unit/test_planner_config.py \
  components/src/dynamo/planner/tests/unit/test_aic_perf_adapter.py \
  components/src/dynamo/planner/tests/unit/test_perf_metrics_priority.py \
  components/src/dynamo/planner/tests/unit/test_offline_bootstrap.py \
  components/src/dynamo/profiler/tests/unit/test_platform_profile.py \
  components/src/dynamo/profiler/tests/unit/test_dgd_platform.py \
  components/src/dynamo/profiler/tests/unit/test_dgd_generation_planner_sla.py \
  components/src/dynamo/profiler/tests/unit/test_dgd_generation_aic.py \
  components/src/dynamo/profiler/tests/unit/test_helpers_profile_sla.py \
  components/src/dynamo/profiler/tests/unit/test_profile_sla_auto_backend.py \
  components/src/dynamo/profiler/tests/unit/test_interpolation.py \
  components/src/dynamo/profiler/tests/unit/test_decode_interpolation_capacity.py \
  components/src/dynamo/profiler/tests/unit/test_offline_profile_pipeline.py \
  components/src/dynamo/profiler/tests/unit/test_profiler_protocol.py \
  adapter/m09/test_load.py \
  adapter/m09/test_online_planning.py \
  adapter/m09/test_offline_render.py
python -m pytest -c adapter/m09/pytest.ini -q \
  components/src/dynamo/vllm/tests/test_pd_contract.py
python -m pytest -c adapter/m09/pytest.ini -q \
  components/src/dynamo/vllm/tests/test_runtime_metadata.py \
  components/src/dynamo/vllm/tests/test_vllm_unit.py \
  -k 'runtime or capacity or cache_info or per_rank'
