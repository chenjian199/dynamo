# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Freeze a separate stronger-latency functional scenario; reuse only verified measured profiles."""
import hashlib
import json
import shutil
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
previous = root / 'profiling-r3'
r = root / 'profiling-r4'
r.mkdir()
assert json.loads((previous / 'audit-result.json').read_text())['passed']
shutil.copytree(previous / 'output', r / 'output')
hashes = {}
for p in (previous / 'output').rglob('*'):
    if p.is_file():
        name = str(p.relative_to(previous / 'output'))
        hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        assert hashlib.sha256((r / 'output' / name).read_bytes()).hexdigest() == hashes[name]
(r / 'profile-origin.json').write_text(json.dumps({'verified_copy': True, 'source': str(previous), 'files': hashes, 'itl_ms': 75, 'previous_itl_ms': 150, 'scope': 'Stricter latency functional autoscaling scenario; not a production SLA or rerun of offline sampling'}, indent=2))
for name in ['input.json', 'offline-profile.py', 'prepare-online.py', 'restart-profile.py']:
    shutil.copy2(previous / name, r / name)
shutil.copy2(root / 'bind-online-reactive-r2.py', r / 'bind-online-r1.py')
for name in ['run-pod.json', 'metrics.json']:
    text = (previous / name).read_text().replace('profiling-r3', 'profiling-r4').replace('m10-metrics-final', 'm10-metrics-reactive').replace('m10-offline-final', 'm10-offline-reactive')
    (r / name).write_text(text)
print('Prepared isolated online scenario with byte-verified profile reuse')
