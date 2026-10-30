#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run on the cluster control host after profiling, with its tools Pod still running.
set -euo pipefail
workdir=${1:?host work directory}
profile=${2:?profile subdirectory}
name=${3:?DGD name}
tools_pod=${4:?tools Pod name}
concurrency=${5:-16}
peak=${6:-1}
worker_source=${7:-}
source_dir=${M09_SOURCE_DIR:-source-v4}
load_options=()
if [[ ${8:-} == token-ids ]]; then load_options+=(--token-id-metrics); fi
source_options=()
if [[ -n "$worker_source" ]]; then source_options+=(--worker-source-dir "$worker_source"); fi
cd "$workdir"
k=(kubectl -n dynamo-m08-1029)
py=("${k[@]}" exec "$tools_pod" -- env
    PYTHONPATH=/m09-deps:/offline/"$source_dir"/components/src:/opt/dynamo/profiler-support
    /opt/dynamo/venv/bin/python)
"${py[@]}" /offline/"$source_dir"/adapter/m09/offline_profile.py \
    --input /offline/input.json --output "/offline/$profile" --reuse-profile \
    > reassemble-v4.log 2>&1
metadata_options=()
if [[ -f prefill-sweep-mdc.json ]]; then metadata_options+=(--prefill-mdc /offline/prefill-sweep-mdc.json); fi
"${py[@]}" /offline/check_offline_profile.py --profile "/offline/$profile" "${metadata_options[@]}" \
    --assembly final_config-reused.yaml \
    --mdc /offline/sweep-mdc.json --output /offline/offline-verification-final.json \
    > offline-verification-final.log 2>&1
"${py[@]}" /offline/prepare_offline_online.py --profile "/offline/$profile" \
    --assembly final_config-reused.yaml --input /offline/input.json \
    --workdir "$workdir" --source-dir "$source_dir" "${source_options[@]}" --health-timeout 120 --output /offline/online-final.yaml
"${k[@]}" apply --dry-run=server -f online-final.yaml > admission.log
"${k[@]}" apply -f online-final.yaml >> admission.log
status=0
python3 campaign.py --name "$name" --tools-pod "$tools_pod" --disagg \
    --load-script load.py --output online-final --isl 512 --osl 128 \
    --concurrency "$concurrency" --duration 300 --recovery 90 \
    --minimum-peak-ready "$peak" --settle-timeout 600 "${load_options[@]}" > online-final.log 2>&1 || status=$?
python3 restart_offline.py --name "$name" --output restart-final > restart-final.log 2>&1 || status=$?
exit "$status"
