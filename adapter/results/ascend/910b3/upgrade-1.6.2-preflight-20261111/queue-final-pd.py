# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Serialize owned final PD acceptance behind the current profiling campaigns."""
import json
import subprocess
import time
from pathlib import Path

r = Path('/data/dynamo-upgrade-1.6.2-20261111')
record = {'started': time.time(), 'completed': [], 'passed': False}
if (r / 'queue-final-pd-status.json').exists():
    record = json.loads((r / 'queue-final-pd-status.json').read_text())
    record['resumed_at'] = time.time()

def wait_for(paths, seconds):
    end = time.monotonic() + seconds
    while not all((r / p).is_file() for p in paths):
        if time.monotonic() > end:
            raise TimeoutError(str(paths))
        time.sleep(15)

def execute(name, seconds):
    with (r / (name + '.queue.log')).open('x') as log:
        p = subprocess.run(['python3', str(r / name)], stdout=log, stderr=subprocess.STDOUT, timeout=seconds)
    record['completed'].append({'script': name, 'exit_code': p.returncode})
    (r / 'queue-final-pd-status.json').write_text(json.dumps(record, indent=2))

try:
    wait_for(['dgdr-r2/autoapply-api-result.json', 'dynamo-deepseek-api-final-r2/result.json'], 5400)
    # Controllers collect their owned workloads before producing completion files.
    cross_result = r / 'cluster-services/pd-cross-final-r1/result.json'
    if not cross_result.exists():
        execute('run-pd-cross-final.py', 4200)
    else:
        assert json.loads(cross_result.read_text())['passed'], 'Previous final cross-PD attempt failed; explicit new attempt needed'
    wait_for(['profiling-r3/online-result.json', 'profiling-r4/online-result.json', 'profiling-r5/online-result.json', 'pd-soak-final-r1/result.json', 'cluster-services/grove-final-r1/result.json', 'pd-split-final-r1/result.json'], 5400)
    # Hybrid needs all 16 NPUs. Do not disturb any still-running test deployment.
    end = time.monotonic() + 600
    while True:
        data = json.loads(subprocess.check_output(['kubectl', '-n', 'heyi-upgrade162', 'get', 'pods', '-o', 'json']))
        busy = [p['metadata']['name'] for p in data['items']
                if p.get('status', {}).get('phase') not in ['Succeeded', 'Failed']
                and any(int(c.get('resources', {}).get('limits', {}).get('huawei.com/Ascend910', 0)) for c in p['spec']['containers'])]
        if not busy:
            break
        if time.monotonic() > end:
            raise TimeoutError('Owned namespace still has NPU pods: ' + repr(busy))
        time.sleep(10)
    execute('run-hybrid-final-r1.py', 5100)
    record['passed'] = all(json.loads((r / p).read_text())['passed'] for p in ['cluster-services/pd-cross-final-r1/result.json', 'hybrid-pd-r1/result.json'])
except Exception as error:
    record['error'] = repr(error)
finally:
    record['finished'] = time.time()
    (r / 'queue-final-pd-status.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
