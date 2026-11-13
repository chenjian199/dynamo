# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independently smoke the DGDR-selected deployment, then remove only its owned resources."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

r = Path('/data/dynamo-upgrade-1.6.2-20261111/dgdr-r2')
k = ['kubectl', '-n', 'heyi-upgrade162']
record = {'passed': False, 'started': time.time()}
owned = []

def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    p.check_returncode()
    return p.stdout

try:
    deadline = time.monotonic() + 3900
    while not (r / 'result.json').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('DGDR controller completion')
        time.sleep(10)
    dgdr = json.loads(run(k + ['get', 'dgdr', 'upgrade-profile-r2', '-o', 'json']))
    owned.append(('dynamographdeploymentrequests', 'upgrade-profile-r2', dgdr['metadata']['uid']))
    (r / 'final-dgdr.json').write_text(json.dumps(dgdr, indent=2))
    assert json.loads((r / 'result.json').read_text())['passed'], 'DGDR failed before autoApply'
    name = dgdr['status']['dgdName']
    dgd = json.loads(run(k + ['get', 'dgd', name, '-o', 'json']))
    # Upstream deliberately leaves DGD independent of DGDR garbage collection.
    # Validate its managed relationship labels, then delete by exact current UID.
    assert dgd['metadata']['labels']['dgdr.nvidia.com/name'] == dgdr['metadata']['name']
    assert dgd['metadata']['labels']['dgdr.nvidia.com/namespace'] == dgdr['metadata']['namespace']
    owned.append(('dynamographdeployments', name, dgd['metadata']['uid']))
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        dgd = json.loads(run(k + ['get', 'dgd', name, '-o', 'json']))
        (r / 'final-dgd.json').write_text(json.dumps(dgd, indent=2))
        if dgd.get('status', {}).get('state') == 'successful':
            break
        time.sleep(10)
    else:
        raise TimeoutError('Selected DGD readiness')
    pods = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))
    frontend = next(p for p in pods['items'] if p['metadata']['labels'].get('nvidia.com/dynamo-component') == 'frontend')
    url = 'http://' + frontend['status']['podIP'] + ':8000'
    with urllib.request.urlopen(url + '/v1/models', timeout=30) as response:
        models = json.load(response)
    (r / 'models.json').write_text(json.dumps(models, indent=2))
    payload = {'model': models['data'][0]['id'], 'messages': [{'role': 'user', 'content': 'What is the capital of France? Answer briefly.'}], 'temperature': 0, 'max_tokens': 64, 'chat_template_kwargs': {'enable_thinking': False}}
    req = urllib.request.Request(url + '/v1/chat/completions', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as response:
        actual = json.load(response)
    (r / 'api.json').write_text(json.dumps({'request': payload, 'response': actual}, indent=2))
    assert 'paris' in actual['choices'][0]['message']['content'].lower()
    assert actual['usage']['completion_tokens'] > 0
    record['passed'] = True
except Exception as error:
    record['error'] = repr(error)
finally:
    for kind, name, uid in reversed(owned):
        if kind == 'dynamographdeployments':
            pods = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))
            (r / 'final-pods.json').write_text(json.dumps(pods, indent=2))
            for pod in pods['items']:
                p = subprocess.run(k + ['logs', pod['metadata']['name'], '--all-containers'], capture_output=True, text=True, timeout=60)
                (r / (pod['metadata']['name'] + '.log')).write_text(p.stdout + p.stderr)
        body = r / (name + '-delete.json')
        body.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}))
        (r / (name + '-delete.log')).write_text(run(k + ['delete', '--raw', '/apis/nvidia.com/v1beta1/namespaces/heyi-upgrade162/' + kind + '/' + name, '-f', str(body)]))
    record['owned'] = owned
    record['finished'] = time.time()
    (r / 'autoapply-api-result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
