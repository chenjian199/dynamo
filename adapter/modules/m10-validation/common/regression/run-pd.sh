#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
platform=${1:?Usage: run-pd.sh PLATFORM OUTPUT MODEL FIVE_DEVICE_ORDINALS}
output=${2:?new output directory}
model=${3:?existing model path}
devices=${4:?five ordinals, explicitly allowing shared devices for the small model}
IFS=, read -r -a cards <<< "$devices"
[[ ${#cards[@]} -eq 5 && ${cards[0]} != "${cards[1]}" ]]
mkdir "$output"
cd /harness
export PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1
python check-installed.py --role runtime > "$output/installed.json"
run_case() {
    local name=$1
    shift
    echo "Starting $name"
    if python adapter/modules/m05-pd-transfer/common/transfer/run.py --platform "$platform" --model "$model" --output "$output/$name" "$@" > "$output/$name.console.log" 2>&1; then
        printf '%s\tpassed\n' "$name" >> "$output/status.tsv"
    else
        printf '%s\tfailed\n' "$name" >> "$output/status.tsv"
        return 1
    fi
}
run_case aggregate --aggregate --devices "${cards[0]}"
run_case pd-2p3d --devices "$devices" --allow-shared-devices --prefill-workers 2 --decode-workers 3 --cancel
if [[ $platform == metax ]]; then
    run_case nixl-2p3d --devices "$devices" --allow-shared-devices --prefill-workers 2 --decode-workers 3 --cancel --connector nixl
fi
run_case soak --devices "${cards[0]},${cards[1]}" --cancel --handoff-case unreachable --soak-seconds 1800 --soak-requests 1000
python - "$output" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
baseline = json.loads((root / 'aggregate/summary.json').read_text())['responses'][0]['choices']
for path in sorted(root.glob('*/summary.json')):
    data = json.loads(path.read_text())
    assert data['status'] == 'passed', path
    for response in data['responses']:
        assert response['choices'] == baseline, f'Aggregate / PD output mismatch: {path}'
(root / 'parity.json').write_text(json.dumps({'passed': True, 'reference': 'aggregate', 'cases': [p.parent.name for p in root.glob('*/summary.json')]}, indent=2) + '\n')
PY
cat "$output/status.tsv"
