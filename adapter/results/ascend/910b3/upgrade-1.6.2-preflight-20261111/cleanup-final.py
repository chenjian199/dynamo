# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Remove only completed campaign objects with UID preconditions; retain installed controllers."""
import json
import subprocess
import time
from pathlib import Path

import yaml

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
out = root / 'cleanup-final'
out.mkdir()
ns = 'heyi-upgrade162'
k = ['kubectl', '-n', ns]
report = {'started': time.time(), 'passed': False, 'deleted': [], 'controllers_retained': True}


def get(kind):
    return json.loads(subprocess.check_output(k + ['get', kind, '-o', 'json'], timeout=60))


def remove(kind, obj, api):
    name, uid = obj['metadata']['name'], obj['metadata']['uid']
    (out / (kind + '-' + name + '.json')).write_text(json.dumps(obj, indent=2) + '\n')
    body = out / (kind + '-' + name + '-delete.json')
    body.write_text(json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}))
    result = subprocess.run(k + ['delete', '--raw', api + name, '-f', str(body)], capture_output=True, text=True, timeout=90)
    (out / (kind + '-' + name + '-delete.log')).write_text(result.stdout + result.stderr)
    result.check_returncode()
    report['deleted'].append({'kind': kind, 'name': name, 'uid': uid})


try:
    assert (root / 'hybrid-pd-r1/result.json').is_file(), 'Hybrid controller not finished'
    assert (root / 'pd-soak-final-r1/result.json').is_file(), 'Soak controller not finished'
    assert (root / 'pd-split-final-r1/result.json').is_file(), 'Split TP controller not finished'
    assert not get('dgd')['items'], 'Model DGD remains; inspect controller cleanup first'
    pods = get('pods')['items']
    active = [p['metadata']['name'] for p in pods
              if p.get('status', {}).get('phase') not in ['Succeeded', 'Failed']
              and any(int(c.get('resources', {}).get('limits', {}).get('huawei.com/Ascend910', 0))
                      for c in p['spec']['containers'])]
    assert not active, active
    config_names = {'m10-capacity', 'upgrade-native-deepseek-script-r1', 'dgdr-output-upgrade-profile-r1'}
    for case in ['profiling-r1', 'profiling-r3', 'profiling-r4', 'profiling-r5']:
        for obj in yaml.safe_load_all((root / case / 'online-r1.yaml').read_text()):
            if obj and obj.get('kind') == 'ConfigMap':
                assert obj['metadata']['namespace'] == ns
                config_names.add(obj['metadata']['name'])
    for obj in get('dgdr')['items']:
        if obj['metadata']['name'] == 'upgrade-profile-r1':
            remove('dgdr', obj, '/apis/nvidia.com/v1beta1/namespaces/' + ns + '/dynamographdeploymentrequests/')
    for obj in pods:
        name = obj['metadata']['name']
        if obj['status']['phase'] in ['Succeeded', 'Failed'] and (
                name == 'm10-offline-tools' or name.startswith(('upgrade-offline-', 'profile-upgrade-profile-'))):
            log = subprocess.run(k + ['logs', name, '--all-containers'], capture_output=True, text=True, timeout=90)
            (out / (name + '.log')).write_text(log.stdout + log.stderr)
            current = [p for p in get('pods')['items'] if p['metadata']['name'] == name]
            if current:  # DGDR owner garbage collection may already have removed it.
                assert current[0]['metadata']['uid'] == obj['metadata']['uid']
                remove('pod', obj, '/api/v1/namespaces/' + ns + '/pods/')
    for obj in get('configmaps')['items']:
        if obj['metadata']['name'] in config_names:
            remove('configmap', obj, '/api/v1/namespaces/' + ns + '/configmaps/')
    for obj in get('podmonitors')['items']:
        if obj['metadata']['name'] in ['m10-metrics', 'm10-metrics-final', 'm10-metrics-reactive', 'm10-metrics-hysteresis']:
            remove('podmonitor', obj, '/apis/monitoring.coreos.com/v1/namespaces/' + ns + '/podmonitors/')
    deadline = time.monotonic() + 120
    while get('dynamoworkermetadata')['items'] and time.monotonic() < deadline:
        time.sleep(5)
    assert not get('dynamoworkermetadata')['items'], 'Discovery worker metadata remains after cleanup'
    for kind in ['pods', 'dgd', 'dgdr', 'configmaps', 'podmonitors', 'dynamoworkermetadata',
                 'services', 'endpoints', 'jobs']:
        (out / ('after-' + kind + '.json')).write_text(json.dumps(get(kind), indent=2) + '\n')
    report['remaining_model_dgds'] = len(get('dgd')['items'])
    report['remaining_worker_metadata'] = len(get('dynamoworkermetadata')['items'])
    report['passed'] = True
except Exception as error:
    report['error'] = repr(error)
finally:
    report['finished'] = time.time()
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
