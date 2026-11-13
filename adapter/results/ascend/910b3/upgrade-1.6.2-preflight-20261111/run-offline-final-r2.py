# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Repeat the unchanged interpolation grid in final images with live metadata capture."""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
r = root / 'profiling-r2'
r.mkdir()
k = ['kubectl', '-n', 'heyi-upgrade162']
env = {**os.environ, 'KUBECONFIG': '/etc/rancher/k3s/k3s.yaml'}
record = {'started': time.time(), 'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb', 'passed': False}
name = 'upgrade-offline-final-r2'

def run(argv):
    p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=90)
    p.check_returncode()
    return p.stdout

try:
    deadline = time.monotonic() + 1800
    while not all((root / 'distribution' / (v + '-1aabe-import-52.json')).is_file() for v in ['runtime', 'tools']):
        if time.monotonic() > deadline:
            raise TimeoutError('Final image import')
        time.sleep(10)
    for filename in ['input.json', 'run-pod.json']:
        text = (root / 'profiling-r1' / filename).read_text().replace('49ef509336', '1aabe435ad').replace('/profiling-r1', '/profiling-r2')
        if filename == 'input.json':
            text = text.replace('m10-offline', 'm10-offline-final')
        obj = json.loads(text)
        if filename == 'run-pod.json':
            obj['metadata']['name'] = name
        (r / filename).write_text(json.dumps(obj, indent=2))
    shutil.copy2(root / 'profiling-r1/offline-profile.py', r / 'offline-profile.py')
    record['inputs'] = {f: hashlib.sha256((r / f).read_bytes()).hexdigest() for f in ['input.json', 'run-pod.json', 'offline-profile.py']}
    (r / 'create.log').write_text(run(k + ['create', '-f', str(r / 'run-pod.json')]))
    snapshots = r / 'snapshots'
    snapshots.mkdir()
    deadline = time.monotonic() + 3650
    while time.monotonic() < deadline:
        stamp = str(time.time_ns())
        for kind in ['dgd', 'dynamoworkermetadata', 'pods']:
            data = run(k + ['get', kind, '-o', 'json'])
            (snapshots / (stamp + '-' + kind + '.json')).write_text(data)
        pod = json.loads(run(k + ['get', 'pod', name, '-o', 'json']))
        record['pod_uid'] = pod['metadata']['uid']
        (r / 'pod-current.json').write_text(json.dumps(pod, indent=2))
        p = subprocess.run(k + ['logs', name], env=env, capture_output=True, text=True, timeout=60)
        (r / 'run.log').write_text(p.stdout + p.stderr)
        if pod['status']['phase'] in ['Succeeded', 'Failed']:
            record['phase'] = pod['status']['phase']
            record['passed'] = pod['status']['phase'] == 'Succeeded' and all((r / 'output' / f).is_file() for f in ['profile-identity.json', 'final_config.yaml'])
            break
        time.sleep(15)
    else:
        raise TimeoutError('Offline final image profiling')
except Exception as e:
    record['error'] = repr(e)
finally:
    record['finished'] = time.time()
    (r / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
