# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Paired official native and final Dynamo pipeline-parallel inference on two NPUs each."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
out = root / 'pp2-final-r1'
out.mkdir()
k = ['kubectl', '-n', 'heyi-upgrade162']
owned = []
record = {'started': time.time(), 'passed': False, 'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb', 'topology': 'TP1 PP2, two NPUs per arm'}

def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    p.check_returncode()
    return p.stdout

try:
    for label, kind, name in [('native', 'pod', 'upgrade-pp2-native'), ('new', 'dgd', 'upgrade-pp2-new')]:
        (out / (label + '-create.log')).write_text(run(k + ['create', '-f', str(root / ('pp2-' + label + '.json'))]))
        obj = json.loads(run(k + ['get', kind, name, '-o', 'json']))
        owned.append((kind, name, obj['metadata']['uid']))
    urls = {}
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline and len(urls) < 2:
        for label, kind, name in [('native', 'pod', 'upgrade-pp2-native'), ('new', 'dgd', 'upgrade-pp2-new')]:
            if label in urls:
                continue
            if kind == 'pod':
                pod = json.loads(run(k + ['get', kind, name, '-o', 'json']))
            else:
                items = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))['items']
                candidates = [p for p in items if p['metadata']['labels'].get('nvidia.com/dynamo-component-type') == 'frontend']
                if not candidates:
                    continue
                pod = candidates[0]
            address = pod.get('status', {}).get('podIP')
            if not address:
                continue
            url = 'http://' + address + ':8000'
            try:
                with urllib.request.urlopen(url + '/v1/models', timeout=3) as f:
                    models = json.load(f)
                if models.get('data'):
                    urls[label] = url
                    (out / (label + '-models.json')).write_text(json.dumps(models, indent=2))
            except (OSError, ValueError):
                pass
        time.sleep(5)
    assert len(urls) == 2, 'Both model APIs must register before paired inference'
    rows = []
    for prompt in ['What is the capital of France? Answer briefly.', 'What is 2+2? Answer only the number.'] * 3:
        payload = {'model': 'upgrade-compare', 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0, 'max_tokens': 64, 'chat_template_kwargs': {'enable_thinking': False}}
        row = {'request': payload}
        for label in ['native', 'new']:
            req = urllib.request.Request(urls[label] + '/v1/chat/completions', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=180) as f:
                row[label] = json.load(f)
            assert row[label]['usage']['completion_tokens'] > 0
        row['exact_parity'] = row['native']['choices'][0]['message']['content'] == row['new']['choices'][0]['message']['content']
        rows.append(row)
        (out / 'responses.json').write_text(json.dumps(rows, indent=2))
        assert row['exact_parity'], row
    record['passed'] = len(rows) == 6
except Exception as error:
    record['error'] = repr(error)
finally:
    pods = json.loads(run(k + ['get', 'pods', '-o', 'json']))
    for pod in pods['items']:
        name = pod['metadata']['name']
        if name.startswith('upgrade-pp2-'):
            (out / (name + '.json')).write_text(json.dumps(pod, indent=2))
            p = subprocess.run(k + ['logs', name, '--all-containers'], capture_output=True, text=True, timeout=60)
            (out / (name + '.log')).write_text(p.stdout + p.stderr)
    for kind, name, uid in reversed(owned):
        body = out / (name + '-delete.json')
        body.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}))
        api = '/api/v1/namespaces/heyi-upgrade162/pods/' if kind == 'pod' else '/apis/nvidia.com/v1beta1/namespaces/heyi-upgrade162/dynamographdeployments/'
        (out / (name + '-delete.log')).write_text(run(k + ['delete', '--raw', api + name, '-f', str(body)]))
    record['owned'] = owned
    record['finished'] = time.time()
    (out / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
