# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run final-image TP1 prefill to TP2 decode after Grove cleanup."""
import json
import os
import subprocess
import time
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
r = root / 'pd-split-final-r1'
r.mkdir()
k = ['kubectl', '-n', 'heyi-upgrade162']
env = {**os.environ, 'KUBECONFIG': '/etc/rancher/k3s/k3s.yaml'}
name = 'upgrade-pd-split-final-r1'
record = {'started': time.time(), 'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb', 'passed': False}
uid = None

def run(argv):
    p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=90)
    p.check_returncode()
    return p.stdout

try:
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        pods = json.loads(run(['kubectl', 'get', 'pods', '-A', '-o', 'json']))['items']
        busy = [p['metadata']['name'] for p in pods if p['spec'].get('nodeName') == 'master-02-45d4a763' and p.get('status', {}).get('phase') not in ['Succeeded', 'Failed'] and any(int(c.get('resources', {}).get('limits', {}).get('huawei.com/Ascend910', 0)) for c in p['spec']['containers'])]
        if (root / 'cluster-services/grove-final-r1/result.json').exists() and not busy:
            break
        time.sleep(10)
    else:
        raise TimeoutError('Grove has not released node52')
    (r / 'create.log').write_text(run(k + ['create', '-f', str(root / 'pd-split-final-r1.json')]))
    deadline = time.monotonic() + 5500
    while time.monotonic() < deadline:
        pod = json.loads(run(k + ['get', 'pod', name, '-o', 'json']))
        uid = pod['metadata']['uid']
        (r / 'pod-current.json').write_text(json.dumps(pod, indent=2))
        logs = subprocess.run(k + ['logs', name], env=env, capture_output=True, text=True, timeout=60)
        (r / 'run.log').write_text(logs.stdout + logs.stderr)
        if pod['status']['phase'] in ['Succeeded', 'Failed']:
            record['phase'] = pod['status']['phase']
            record['passed'] = pod['status']['phase'] == 'Succeeded'
            break
        time.sleep(15)
    else:
        raise TimeoutError('Final image hardware regression')
except Exception as e:
    record['error'] = repr(e)
finally:
    if uid:
        body = r / 'delete.json'
        body.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}))
        (r / 'delete.log').write_text(run(k + ['delete', '--raw', '/api/v1/namespaces/heyi-upgrade162/pods/' + name, '-f', str(body)]))
        record['pod_uid'] = uid
    record['finished'] = time.time()
    (r / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
