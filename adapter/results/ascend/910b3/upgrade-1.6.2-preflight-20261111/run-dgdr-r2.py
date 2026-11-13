# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the production 1aabe DGDR Job and capture every candidate before deletion."""
import json
import os
import subprocess
import time
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
r = root / 'dgdr-r2'
r.mkdir(exist_ok=True)
k = ['kubectl', '-n', 'heyi-upgrade162']
env = {**os.environ, 'KUBECONFIG': '/etc/rancher/k3s/k3s.yaml'}
record = {'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb', 'started': time.time(), 'passed': False}
name = 'upgrade-profile-r2'

def run(argv, timeout=60):
    p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
    p.check_returncode()
    return p.stdout

def get(kind, obj=None):
    return json.loads(run(k + ['get', kind] + ([obj] if obj else []) + ['-o', 'json']))

try:
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        if all((root / 'distribution' / (x + '-1aabe-import-52.json')).exists() for x in ['runtime', 'tools']):
            break
        time.sleep(10)
    else:
        raise TimeoutError('Final runtime/tools imports')
    original = (root / 'profiling-r1/dgdr.json').read_text()
    manifest = json.loads(original.replace('49ef509336', '1aabe435ad').replace('upgrade-profile-r1', name).replace('/dgdr-r1/', '/dgdr-r2/'))
    (r / 'dgdr.json').write_text(json.dumps(manifest, indent=2))
    (r / 'create.log').write_text(run(k + ['create', '-f', str(r / 'dgdr.json')]))
    record['uid'] = get('dgdr', name)['metadata']['uid']
    deadline = time.monotonic() + 3900
    snapshots = r / 'snapshots'
    snapshots.mkdir(exist_ok=True)
    while time.monotonic() < deadline:
        status = get('dgdr', name)
        (r / 'status.json').write_text(json.dumps(status, indent=2))
        stamp = str(time.time_ns())
        for kind in ['dgd', 'dynamoworkermetadata', 'pods']:
            obj = get(kind)
            (snapshots / (stamp + '-' + kind + '.json')).write_text(json.dumps(obj, indent=2))
            if kind == 'pods':
                for pod in obj['items']:
                    pn = pod['metadata']['name']
                    if pn.startswith('profile-' + name) or pod['metadata'].get('labels', {}).get('nvidia.com/dynamo-graph-deployment-name', '').startswith(name):
                        p = subprocess.run(k + ['logs', pn, '--all-containers'], env=env, capture_output=True, text=True, timeout=60)
                        (r / (pn + '.log')).write_text(p.stdout + p.stderr)
        phase = status.get('status', {}).get('phase')
        if phase in ['Failed', 'Deployed']:
            record['phase'] = phase
            record['passed'] = phase == 'Deployed'
            break
        time.sleep(15)
    else:
        raise TimeoutError('DGDR full profiling deadline')
except Exception as e:
    record['error'] = repr(e)
finally:
    record['finished'] = time.time()
    (r / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
