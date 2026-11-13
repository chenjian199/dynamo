# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Replay the exact ready DGDR-selected spec after correcting only API client discovery."""
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111/dgdr-r2')
out = root / 'selected-api-r3'
out.mkdir()
k = ['kubectl', '-n', 'heyi-upgrade162']
name = 'upgrade-profile-selected-r3'
uid = None
record = {'started': time.time(), 'passed': False, 'note': 'DGDR autoApply was Ready; first client assumed lowercase component name. This replays identical selected spec under a new owned DGD, not a new profiling campaign.'}

def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    p.check_returncode()
    return p.stdout

try:
    source = json.loads((root / 'final-dgd.json').read_text())
    assert source['status']['state'] == 'successful'
    record['original_ready_uid'] = source['metadata']['uid']
    record['selected_spec_sha256'] = hashlib.sha256(json.dumps(source['spec'], sort_keys=True).encode()).hexdigest()
    manifest = {key: source[key] for key in ['apiVersion', 'kind', 'spec']}
    manifest['metadata'] = {'name': name, 'namespace': 'heyi-upgrade162', 'labels': {'heyi.io/campaign': 'upgrade-1.6.2-ascend-20261111'}, 'annotations': {key: value for key, value in source['metadata']['annotations'].items() if key in ['nvidia.com/dynamo-discovery-backend', 'nvidia.com/enable-grove', 'nvidia.com/workload-provider']}}
    path = out / 'dgd.json'
    path.write_text(json.dumps(manifest, indent=2))
    (out / 'create.log').write_text(run(k + ['create', '-f', str(path)]))
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        obj = json.loads(run(k + ['get', 'dgd', name, '-o', 'json']))
        uid = obj['metadata']['uid']
        (out / 'ready.json').write_text(json.dumps(obj, indent=2))
        if obj.get('status', {}).get('state') == 'successful':
            break
        time.sleep(10)
    else:
        raise TimeoutError('Selected spec replay readiness')
    pods = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))
    frontends = [p for p in pods['items'] if p['metadata']['labels'].get('nvidia.com/dynamo-component-type') == 'frontend']
    assert len(frontends) == 1
    url = 'http://' + frontends[0]['status']['podIP'] + ':8000'
    deadline = time.monotonic() + 180
    model_observations = []
    while time.monotonic() < deadline:
        with urllib.request.urlopen(url + '/v1/models', timeout=30) as f:
            models = json.load(f)
        model_observations.append({'time': time.time(), 'models': models})
        (out / 'model-discovery.json').write_text(json.dumps(model_observations, indent=2))
        if models.get('data'):
            break
        time.sleep(3)
    else:
        raise TimeoutError('Frontend model discovery after Pod readiness')
    (out / 'models.json').write_text(json.dumps(models, indent=2))
    rows = []
    for i in range(3):
        payload = {'model': models['data'][0]['id'], 'messages': [{'role': 'user', 'content': 'What is the capital of France? Answer briefly.'}], 'temperature': 0, 'max_tokens': 64, 'chat_template_kwargs': {'enable_thinking': False}}
        request = urllib.request.Request(url + '/v1/chat/completions', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=120) as f:
            response = json.load(f)
        rows.append({'request': payload, 'response': response})
        (out / 'api.json').write_text(json.dumps(rows, indent=2))
        assert 'paris' in response['choices'][0]['message']['content'].lower()
        assert response['usage']['completion_tokens'] > 0
    record['passed'] = len(rows) == 3
except Exception as error:
    record['error'] = repr(error)
finally:
    if uid:
        pods = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))
        (out / 'pods.json').write_text(json.dumps(pods, indent=2))
        for pod in pods['items']:
            p = subprocess.run(k + ['logs', pod['metadata']['name'], '--all-containers'], capture_output=True, text=True, timeout=60)
            (out / (pod['metadata']['name'] + '.log')).write_text(p.stdout + p.stderr)
        body = out / 'delete.json'
        body.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}))
        (out / 'delete.log').write_text(run(k + ['delete', '--raw', '/apis/nvidia.com/v1beta1/namespaces/heyi-upgrade162/dynamographdeployments/' + name, '-f', str(body)]))
    record['finished'] = time.time()
    (out / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
