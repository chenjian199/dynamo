# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded checks of packaged artifacts only; preserve failures and container cleanup."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
work = root / 'candidate-a97c'
out = work / 'runtime-smoke-r1'
out.mkdir()
env = {**os.environ, 'PATH': '/data/dynamo-m10/docker-bin:' + os.environ['PATH'],
       'DOCKER_HOST': 'unix:///run/dynamo-m10-docker.sock', 'DOCKER_CONFIG': '/data/dynamo-m10/docker-client'}
record = {'source_commit': 'a97c71d2b17feba59ce73da5b2844e604b3b684b', 'cases': [], 'certified': False}
cid = None
try:
    deadline = time.monotonic() + 3600
    while not (work / 'images.json').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('Runtime image is not ready')
        failure = work / 'runtime-build.log.exit'
        if failure.exists() and failure.read_text().strip() != '0':
            raise RuntimeError('Runtime build failed')
        time.sleep(10)
    image = json.loads((work / 'images.json').read_text())['runtime']
    record['image'] = image
    health = subprocess.check_output(['npu-smi', 'info'], text=True, timeout=30)
    (out / 'npu-before.txt').write_text(health)
    for device in range(5):
        assert f'No running processes found in NPU {device}' in health, health
    testdir = work / 'hardware-harness'
    testdir.mkdir()
    archive = root / 'hardware-harness-a97c.tar.gz'
    record['harness_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    subprocess.run(['tar', '-xzf', str(archive), '-C', str(testdir)], check=True)
    argv = ['docker', 'run', '-d', '--name', 'heyi-upgrade-162-runtime-r5-smoke', '--network', 'host',
            '--shm-size', '16g', '--user', '0', '-e', 'OMP_NUM_THREADS=4', '-e', 'HF_HUB_OFFLINE=1',
            '-e', 'PYTORCH_NPU_ALLOC_CONF=expandable_segments:True',
            '--mount', f'type=bind,src={testdir},dst=/test,readonly',
            '--mount', f'type=bind,src={out},dst=/evidence',
            '--mount', 'type=bind,src=/data/weight/Qwen3-0.6B,dst=/models/Qwen3-0.6B,readonly']
    for d in range(5):
        argv += ['--device', f'/dev/davinci{d}']
    for p in ['/dev/davinci_manager', '/dev/devmm_svm', '/dev/hisi_hdc']:
        argv += ['--device', p]
    for p in ['/usr/local/Ascend/driver', '/usr/local/dcmi', '/usr/local/sbin/npu-smi']:
        argv += ['--mount', f'type=bind,src={p},dst={p},readonly']
    argv += [image['id'], 'sleep', 'infinity']
    cid = subprocess.check_output(argv, env=env, text=True).strip()
    record['container_id'] = cid
    (out / 'ownership.json').write_text(json.dumps({'argv': argv, 'id': cid}, indent=2) + '\n')
    commands = [
        ('cpu-platform', ['python', '/test/container/platforms/cpu-smoke.py'], 60),
        ('native-kvbm', ['python', '/test/container/platforms/native-smoke.py', '--platform', 'ascend',
                         '--devices', '0,1', '--output', '/evidence/native-kvbm'], 1200),
        ('kv-routing', ['python', '/test/container/platforms/smoke.py', '--platform', 'ascend',
                       '--devices', '0,1', '--model', '/models/Qwen3-0.6B', '--output', '/evidence/kv-routing'], 1200),
    ]
    for name, command, limit in commands:
        argv = ['docker', 'exec', cid, '/opt/dynamo/bin/entrypoint.sh', 'timeout', str(limit), *command]
        with (out / f'{name}.log').open('x') as log:
            rc = subprocess.run(argv, env=env, stdout=log, stderr=subprocess.STDOUT,
                                timeout=limit+60).returncode
        record['cases'].append({'name': name, 'argv': argv, 'exit_code': rc})
        (out / 'status.json').write_text(json.dumps(record, indent=2) + '\n')
        if rc:
            break
finally:
    if cid:
        result = subprocess.run(['docker', 'rm', '-f', cid], env=env, capture_output=True, text=True, timeout=60)
        record['cleanup'] = {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    (out / 'status.json').write_text(json.dumps(record, indent=2) + '\n')
