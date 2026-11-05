#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
output=${1:?output directory}
mkdir -p "$output"
/opt/dynamo/venv/bin/python /harness/check-installed.py > "$output/installed.json"
python -m pip check > "$output/tools-pip-check.txt" || {
    # CUDA/MetaX inherit documented vendor conflicts; their image build rejects
    # additions. The isolated CPU environment must be completely consistent.
    [[ ! -d /opt/dynamo/tools-venv ]]
}
python -c 'import hashlib, importlib.metadata as m, json, sys; from pathlib import Path; import dynamo._core as c; print(json.dumps({"executable":sys.executable,"core_sha256":hashlib.sha256(Path(c.__file__).read_bytes()).hexdigest(),"packages":{p:m.version(p) for p in ("ai-dynamo","ai-dynamo-runtime","aisimulate","aiperf")}},indent=2))' > "$output/tools-environment.json"
python /harness/container/platforms/cpu-smoke.py > "$output/cpu.json"
export PYTHONPATH=/harness/test-deps${PYTHONPATH:+:$PYTHONPATH}
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --noconftest -p pytest_asyncio.plugin -p pytest_timeout -p no:cacheprovider -c /unit/pytest.ini /unit --junitxml="$output/contracts.xml"
