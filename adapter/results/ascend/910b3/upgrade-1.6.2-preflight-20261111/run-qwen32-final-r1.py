# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Replay the six retained Qwen32 TP4 acceptance requests on the final image."""
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
r = root / 'qwen32-final-r1'
r.mkdir()
k = ['kubectl', '-n', 'heyi-upgrade162']
name = 'upgrade-qwen32-final-r1'
uid = None
control = root / 'kubernetes-preflight/qwen32-smoke-r1/requests.json'
record = {'started': time.time(), 'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb', 'passed': False, 'control_sha256': hashlib.sha256(control.read_bytes()).hexdigest(), 'requests': []}

def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    p.check_returncode()
    return p.stdout

try:
    (r / 'create.log').write_text(run(k + ['create', '-f', str(root / 'qwen32-final-r1.json')]))
    deadline = time.monotonic() + 1500
    while time.monotonic() < deadline:
        dgd = json.loads(run(k + ['get', 'dgd', name, '-o', 'json']))
        uid = dgd['metadata']['uid']
        (r / 'dgd-current.json').write_text(json.dumps(dgd, indent=2))
        if dgd.get('status', {}).get('state') == 'successful':
            break
        time.sleep(10)
    else:
        raise TimeoutError('Qwen32 TP4 ready')
    pods = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))
    frontend = next(p for p in pods['items'] if p['metadata']['labels'].get('nvidia.com/dynamo-component') == 'frontend')
    url = 'http://' + frontend['status']['podIP'] + ':8000/v1/chat/completions'
    for expected in json.loads(control.read_text()):
        payload = expected['request']
        started = time.monotonic()
        request = urllib.request.Request(url, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=180) as response:
            actual = json.load(response)
            item = {'request': payload, 'response': actual, 'http_status': response.status, 'seconds': time.monotonic() - started}
        item['exact_parity'] = actual['choices'][0]['message']['content'] == expected['response']['choices'][0]['message']['content']
        record['requests'].append(item)
        assert item['exact_parity'] and actual['usage']['completion_tokens'] > 0
    record['passed'] = len(record['requests']) == 6
except Exception as e:
    record['error'] = repr(e)
finally:
    if uid:
        pods = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))
        (r / 'pods.json').write_text(json.dumps(pods, indent=2))
        for pod in pods['items']:
            p = subprocess.run(k + ['logs', pod['metadata']['name']], capture_output=True, text=True, timeout=60)
            (r / (pod['metadata']['name'] + '.log')).write_text(p.stdout + p.stderr)
        body = r / 'delete.json'
        body.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}))
        (r / 'delete.log').write_text(run(k + ['delete', '--raw', '/apis/nvidia.com/v1beta1/namespaces/heyi-upgrade162/dynamographdeployments/' + name, '-f', str(body)]))
        record['uid'] = uid
    record['finished'] = time.time()
    (r / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps({'passed': record['passed'], 'requests': len(record['requests']), 'error': record.get('error')}), flush=True)
