# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Owned final-image two-node Hybrid PD lifecycle acceptance, with bounded cleanup."""
import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
source = Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111')
out = root / 'hybrid-pd-r1'
out.mkdir()
name = 'upgrade-hybrid-pd-r1'
k = ['kubectl', '-n', 'heyi-upgrade162']
uid = None
record = {'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb',
          'controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'started': time.time(), 'passed': False, 'scope': 'Hybrid PD boundary, concurrent stream, cancellation and recovery; not numerical or SLO certification'}

def run(argv, timeout=60, input=None):
    result = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout)
    result.check_returncode()
    return result.stdout

try:
    manifest = root / 'hybrid-pd-r1.json'
    record['manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (out / 'create.log').write_text(run(k + ['create', '-f', str(manifest)]))
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        dgd = json.loads(run(k + ['get', 'dgd', name, '-o', 'json']))
        uid = dgd['metadata']['uid']
        (out / 'dgd-current.json').write_text(json.dumps(dgd, indent=2))
        if dgd.get('status', {}).get('state') == 'successful':
            break
        pending = json.loads(run(k + ['get', 'pods', '-l',
                                      'nvidia.com/dynamo-graph-deployment-name=' + name,
                                      '-o', 'json']))
        failed = [p['metadata']['name'] for p in pending['items']
                  if p.get('status', {}).get('phase') == 'Failed'
                  or any(c.get('restartCount', 0) >= 2
                         and c.get('lastState', {}).get('terminated', {}).get('exitCode', 0) != 0
                         for c in p.get('status', {}).get('containerStatuses', []))]
        if failed:
            raise RuntimeError('Repeated worker startup failure: ' + repr(failed))
        time.sleep(15)
    else:
        raise TimeoutError('Two-node TP8+TP8 Hybrid PD readiness')
    pods = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))
    frontend = next(p for p in pods['items'] if p['metadata']['labels'].get('nvidia.com/dynamo-component') == 'frontend')
    helper = source / 'adapter/modules/m10-validation/common/regression/hybrid-pd-client.py'
    load = source / 'adapter/modules/m09-telemetry-planner/common/online/load.py'
    record['helpers'] = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [helper, load]}
    script = 'import base64,sys,runpy\nfrom pathlib import Path\np=Path("/tmp/heyi-hybrid-final-r1");p.mkdir()\n'
    for path, filename in [(helper, 'client.py'), (load, 'load.py')]:
        script += '(p/' + repr(filename) + ').write_bytes(base64.b64decode(' + repr(base64.b64encode(path.read_bytes()).decode()) + '))\n'
    script += 'sys.argv=[str(p/"client.py"),"--url","http://127.0.0.1:8000","--model","upgrade-deepseek","--load-script",str(p/"load.py"),"--output",str(p/"result.json")]\nrunpy.run_path(str(p/"client.py"),run_name="__main__")\n'
    client = subprocess.run(k + ['exec', '-i', frontend['metadata']['name'], '--', '/opt/dynamo/venv/bin/python', '-'], input=script, capture_output=True, text=True, timeout=2700)
    (out / 'client.log').write_text(client.stdout + client.stderr)
    lines = [line.removeprefix('HYBRID_PD_RESULT=') for line in client.stdout.splitlines() if line.startswith('HYBRID_PD_RESULT=')]
    if lines:
        result = json.loads(lines[-1])
        (out / 'client-result.json').write_text(json.dumps(result, indent=2))
        record['client_passed'] = result['passed'] and len(result['requests']) == 11 and result['cancellations'] == 4
    record['client_exit_code'] = client.returncode
    record['passed'] = client.returncode == 0 and record.get('client_passed', False)
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
        record['uid'] = uid
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            remaining = json.loads(run(k + ['get', 'pods', '-l', 'nvidia.com/dynamo-graph-deployment-name=' + name, '-o', 'json']))
            if not remaining['items']:
                break
            time.sleep(5)
        (out / 'cleanup-observed.json').write_text(json.dumps(remaining, indent=2))
        record['cleanup_passed'] = not remaining['items']
        record['passed'] = record['passed'] and record['cleanup_passed']
    record['finished'] = time.time()
    (out / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
