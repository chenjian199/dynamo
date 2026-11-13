# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-node NPU byte parity plus unreset host RoCE counters, using final wheels."""
import concurrent.futures
import copy
import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parent
out = root / 'rdma-final-r1'
out.mkdir()
source = Path('/home/metax/CJ_workspace/dynamo/.worktrees/source-1.6.2-ascend-20261111/.worktrees/dynamo-1.6.2-ascend-20261111')
probe = (source / 'adapter/modules/m08-kubernetes/common/rdma/transfer.py').read_text()
counters = (source / 'adapter/modules/m08-kubernetes/common/rdma/counters.py').read_text()
ssh = ['ssh', '-F', '/var/tmp/dynamo-regression-1028/ssh-config-upgrade-fresh']
k = ['kubectl', '-n', 'heyi-upgrade162']
record = {'started': time.time(), 'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb', 'passed': False, 'probe_sha256': hashlib.sha256(probe.encode()).hexdigest()}
owned = []

def remote(host, argv, data=None, timeout=60):
    return subprocess.run(ssh + [host, shlex.join(argv)], input=data, capture_output=True, text=True, timeout=timeout)

def checked(host, argv, data=None, timeout=60):
    p = remote(host, argv, data, timeout)
    p.check_returncode()
    return p.stdout

def capture(phase):
    result = {}
    for host in ['huawei-52', 'huawei-55']:
        p = remote(host, ['python3', '-', '--platform', 'ascend'], counters, 190)
        (out / (host + '-' + phase + '.json')).write_text(p.stdout)
        (out / (host + '-' + phase + '.stderr')).write_text(p.stderr)
        p.check_returncode()
        result[host] = json.loads(p.stdout)
    return result

try:
    pod_template = json.loads((root / 'comparison-r1/native.json').read_text())
    for host, node in [('52', 'master-02-45d4a763'), ('55', 'master')]:
        pod = copy.deepcopy(pod_template)
        name = 'upgrade-rdma-final-' + host
        pod['metadata'] = {'name': name, 'namespace': 'heyi-upgrade162', 'labels': {'heyi.io/campaign': 'upgrade-1.6.2-ascend-20261111'}}
        spec = pod['spec']
        spec['nodeSelector']['kubernetes.io/hostname'] = node
        spec['activeDeadlineSeconds'] = 900
        c = spec['containers'][0]
        c['image'] = 'heyi/dynamo:1.6.2-ascend0.30-1aabe435ad-runtime'
        c['command'] = ['/opt/dynamo/bin/entrypoint.sh']
        c['args'] = ['sleep', '850']
        c.pop('readinessProbe', None)
        c['env'] += [{'name': 'DYN_PLATFORM', 'value': 'ascend'}]
        c['volumeMounts'] = [m for m in c['volumeMounts'] if m['name'] != 'models']
        spec['volumes'] = [v for v in spec['volumes'] if v['name'] != 'models']
        (out / (name + '.json')).write_text(json.dumps(pod, indent=2))
        checked('huawei-52', k + ['create', '-f', '-'], json.dumps(pod))
        actual = json.loads(checked('huawei-52', k + ['get', 'pod', name, '-o', 'json']))
        owned.append((name, actual['metadata']['uid']))
    ready = []
    for name, uid in owned:
        checked('huawei-52', k + ['wait', '--for=condition=Ready', 'pod/' + name, '--timeout=300s'], timeout=320)
        pod = json.loads(checked('huawei-52', k + ['get', 'pod', name, '-o', 'json']))
        (out / (name + '-ready.json')).write_text(json.dumps(pod, indent=2))
        ready.append(pod)
    idle = capture('idle')
    time.sleep(5)
    before = capture('before')
    peer = ready[0]['status']['podIP']
    def transfer(rank):
        pod = ready[rank]
        argv = k + ['exec', '-i', pod['metadata']['name'], '--', '/opt/dynamo/bin/entrypoint.sh', 'python', '-', '--platform', 'ascend', '--rank', str(rank), '--host', pod['status']['podIP'], '--peer', peer]
        (out / ('rank-' + str(rank) + '-argv.json')).write_text(json.dumps(argv, indent=2))
        p = remote('huawei-52', argv, probe, 300)
        (out / ('rank-' + str(rank) + '.log')).write_text(p.stdout + p.stderr)
        p.check_returncode()
        result = json.loads(next(line.split('=', 1)[1] for line in p.stdout.splitlines() if line.startswith('RDMA_PROBE_RESULT=')))
        assert result['passed'] and len(result['cases']) == 20 and result['device_allocated_after'] == 0
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        record['ranks'] = list(pool.map(transfer, [0, 1]))
    after = capture('after')
    deltas = {}
    for host in before:
        deltas[host] = {}
        for device, values in before[host]['devices'].items():
            deltas[host][device] = {key: {'idle_delta': value - idle[host]['devices'][device].get(key, value), 'transfer_delta': after[host]['devices'][device][key] - value} for key, value in values.items() if key in after[host]['devices'][device] and ('roce' in key.lower() or 'err' in key.lower())}
    (out / 'counter-deltas.json').write_text(json.dumps(deltas, indent=2))
    record['byte_parity_passed'] = True
    record['roce_activity_observed'] = all(any(v['transfer_delta'] > 0 for dev in host.values() for key, v in dev.items() if key in ['roce_tx_rc_pkt_num', 'roce_rx_rc_pkt_num']) for host in deltas.values())
    record['passed'] = record['roce_activity_observed']
except Exception as e:
    record['error'] = repr(e)
finally:
    record['cleanup'] = []
    for name, uid in reversed(owned):
        body = json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}})
        p = remote('huawei-52', k + ['delete', '--raw', '/api/v1/namespaces/heyi-upgrade162/pods/' + name, '-f', '-'], body)
        (out / (name + '-delete.log')).write_text(p.stdout + p.stderr)
        record['cleanup'].append({'name': name, 'uid': uid, 'exit_code': p.returncode})
    record['finished'] = time.time()
    (out / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
