# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded replay of final 1aabe profiles with upstream reactive and throughput scaling."""
import json
import os
import subprocess
import time
from pathlib import Path

r = Path('/data/dynamo-upgrade-1.6.2-20261111/profiling-r3')
s = Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111')
k = ['kubectl', '-n', 'heyi-upgrade162']
env = {**os.environ, 'KUBECONFIG': '/etc/rancher/k3s/k3s.yaml'}
name = 'upgrade-offline-final-reuse-r1'
record = {'started': time.time(), 'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb', 'passed': False}
owned = []

def run(argv, timeout=60, log=None):
    p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
    if log:
        (r / log).write_text(p.stdout + p.stderr)
    p.check_returncode()
    return p.stdout

def get(kind, obj):
    return json.loads(run(k + ['get', kind, obj, '-o', 'json']))

try:
    deadline = time.monotonic() + 3900
    while not (r / 'result.json').is_file():
        if time.monotonic() > deadline:
            raise TimeoutError('Final offline profiles not ready')
        time.sleep(10)
    assert json.loads((r / 'result.json').read_text())['passed']
    pod = json.loads((r / 'run-pod.json').read_text())
    pod['metadata']['name'] = name
    pod['spec']['activeDeadlineSeconds'] = 4200
    pod['spec']['containers'][0]['command'] = ['bash', '-ec',
        '/opt/dynamo/tools-venv/bin/python /offline/offline-profile.py --input /offline/input.json --output /offline/output --reuse-profile && '
        '/opt/dynamo/tools-venv/bin/python /offline/prepare-online.py --input /offline/input.json --profile /offline/output --output /offline/online-r1.yaml && '
        '/opt/dynamo/tools-venv/bin/python /offline/bind-online-r1.py && touch /offline/reuse-r1.ready && sleep 3000']
    (r / 'reuse-pod.json').write_text(json.dumps(pod, indent=2))
    run(k + ['create', '-f', str(r / 'reuse-pod.json')], log='reuse-create.log')
    owned.append(('pod', name, get('pod', name)['metadata']['uid']))
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if (r / 'reuse-r1.ready').exists():
            break
        state = get('pod', name)
        if state.get('status', {}).get('phase') in ['Failed', 'Succeeded']:
            raise RuntimeError('Reuse pod exited before completing profile reuse')
        time.sleep(5)
    else:
        raise TimeoutError('Reuse profile readiness')
    run(k + ['apply', '-f', str(r / 'metrics.json')], log='metrics-apply.log')
    run(k + ['apply', '-f', str(r / 'online-r1.yaml')], log='online-apply.log')
    owned.append(('dgd', 'm10-offline-final', get('dgd', 'm10-offline-final')['metadata']['uid']))
    argv = ['python3', str(s / 'adapter/modules/m09-telemetry-planner/common/online/campaign.py'),
        '--namespace', 'heyi-upgrade162', '--name', 'm10-offline-final', '--tools-pod', name,
        '--output', str(r / 'online'), '--load-script', str(s / 'adapter/modules/m09-telemetry-planner/common/online/load.py'),
        '--duration', '600', '--recovery', '120', '--concurrency', '16', '--isl', '512', '--osl', '128', '--minimum-peak-ready', '2', '--disagg']
    (r / 'online-argv.json').write_text(json.dumps(argv, indent=2))
    campaign = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=3000)
    (r / 'online-campaign.log').write_text(campaign.stdout + campaign.stderr)
    record['campaign_exit_code'] = campaign.returncode
    run(['python3', str(r / 'restart-profile.py'), '--namespace', 'heyi-upgrade162', '--name', 'm10-offline-final', '--output', str(r / 'restart')], 660, 'restart.log')
    record['passed'] = json.loads((r / 'online/result.json').read_text())['passed'] and json.loads((r / 'restart/result.json').read_text())['passed']
except Exception as e:
    record['error'] = repr(e)
finally:
    for kind in ['pods', 'dgd', 'dynamoworkermetadata', 'configmaps']:
        p = subprocess.run(k + ['get', kind, '-o', 'json'], env=env, capture_output=True, text=True)
        (r / ('online-final-' + kind + '.json')).write_text(p.stdout + p.stderr)
    pods = json.loads(run(k + ['get', 'pods', '-o', 'json']))['items']
    for pod in pods:
        n = pod['metadata']['name']
        if n == name or pod['metadata'].get('labels', {}).get('nvidia.com/dynamo-graph-deployment-name') == 'm10-offline-final':
            p = subprocess.run(k + ['logs', n, '--all-containers'], env=env, capture_output=True, text=True, timeout=60)
            (r / ('online-' + n + '.log')).write_text(p.stdout + p.stderr)
    for kind, obj, uid in reversed(owned):
        body = r / (obj + '-delete.json')
        body.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}))
        api = '/api/v1/namespaces/heyi-upgrade162/pods/' if kind == 'pod' else '/apis/nvidia.com/v1beta1/namespaces/heyi-upgrade162/dynamographdeployments/'
        run(k + ['delete', '--raw', api + obj, '-f', str(body)], log=obj + '-delete.log')
    record['finished'] = time.time()
    (r / 'online-result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
