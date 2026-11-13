# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fresh packaged-native diagnostic; not a substitute for the frozen final campaign."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
source = root / 'source-50424baf69'
output = Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/native-50424baf69')
output.mkdir(parents=True, exist_ok=False)
image = 'sha256:fa278f83929c920a82fb8f797ff1cf54c40440f7f39f6c74ef3e1742f2919599'
script = source / 'container/platforms/native-smoke.py'
manifest = json.loads((source / 'SOURCE.json').read_text())
assert hashlib.sha256(script.read_bytes()).hexdigest() == manifest['files']['container/platforms/native-smoke.py']
env = {**os.environ, 'DOCKER_HOST': 'unix:///run/dynamo-m10-docker.sock'}
docker = '/data/dynamo-m10/docker-bin/docker'
argv = [docker, 'run', '--rm', '--name', 'heyi-upgrade162-native-50424baf69', '--network', 'none',
        '--device', '/dev/davinci0', '--device', '/dev/davinci1', '--device', '/dev/davinci_manager',
        '--device', '/dev/devmm_svm', '--device', '/dev/hisi_hdc',
        '--mount', 'type=bind,src=/usr/local/Ascend/driver,dst=/usr/local/Ascend/driver,readonly',
        '--mount', f'type=bind,src={script},dst=/test/native-smoke.py,readonly',
        '--mount', f'type=bind,src={output},dst=/results', image,
        'python', '/test/native-smoke.py', '--platform', 'ascend', '--devices', '0,1', '--output', '/results/cases']
with (output / 'npu-before.txt').open('x') as f:
    subprocess.run(['npu-smi', 'info'], stdout=f, stderr=subprocess.STDOUT, check=True, timeout=30)
record = {'source_commit': manifest['commit'], 'image_id': image, 'argv': argv,
          'harness_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
          'qualification': 'packaged native diagnostic; final scoped campaign still pending'}
(output / 'command.json').write_text(json.dumps(record, indent=2) + '\n')
with (output / 'console.log').open('x') as f:
    result = subprocess.run(argv, env=env, stdout=f, stderr=subprocess.STDOUT, check=False, timeout=1500)
record['returncode'] = result.returncode
# Docker --rm completion alone is not absence proof: inspect the owned name again.
left = subprocess.check_output([docker, 'ps', '-aq', '--filter', 'name=^/heyi-upgrade162-native-50424baf69$'], env=env, text=True)
record['remaining_container_ids'] = left.split()
with (output / 'npu-after.txt').open('x') as f:
    subprocess.run(['npu-smi', 'info'], stdout=f, stderr=subprocess.STDOUT, check=True, timeout=30)
record['files'] = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.rglob('*')) if p.is_file()}
(output / 'receipt.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'returncode': result.returncode, 'remaining': record['remaining_container_ids'], 'output': str(output)}))
raise SystemExit(result.returncode or bool(left.strip()))
