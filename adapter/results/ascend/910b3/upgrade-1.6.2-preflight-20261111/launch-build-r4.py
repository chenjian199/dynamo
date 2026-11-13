# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run on Huawei 52 after the previous owned builder has stopped compiling."""
import hashlib
import json
import pathlib
import shutil
import subprocess

root = pathlib.Path('/data/dynamo-upgrade-1.6.2-20261111')
source = pathlib.Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111')
old = 'heyi-upgrade-162-ascend-builder-20261111'
top = subprocess.check_output(['docker', 'top', old, '-eo', 'pid,comm'], text=True)
assert [line.split()[-1] for line in top.splitlines()[1:]] == ['sleep'], top
delta = root / 'preliminary/ascend-source-r3-delta.tar.gz'
assert hashlib.sha256(delta.read_bytes()).hexdigest() == 'd2b43518f7a00c52ab16db490806d83912978f991513c28ba37c821691b709ce'
subprocess.run(['tar', '-xzf', str(delta), '-C', str(source)], check=True)
manifest = json.loads((source / 'SOURCE.json').read_text())
assert manifest['commit'] == 'c753fad8eec66440770c3101606517cc9e3fb181'
for name, expected in manifest['files'].items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, name
out = root / 'candidate-c753/artifacts'
out.mkdir(parents=True, exist_ok=False)
(out / 'wheels').mkdir()
for wheel in (root / 'preliminary/artifacts/wheels').glob('*.whl'):
    if not wheel.name.startswith(('ai_dynamo', 'kvbm-', 'gpu_memory_service')):
        shutil.copy2(wheel, out / 'wheels' / wheel.name)
name = 'heyi-upgrade-162-ascend-builder-r4-20261111'
image = 'sha256:74224d25260decde6abdd26c6ebc383faadbaf792115631bdbe966793742c4db'
argv = ['docker', 'run', '-d', '--name', name, '--network', 'host', '--user', '0',
        '--mount', f'type=bind,src={root / "cache"},dst=/build-cache',
        '--mount', f'type=bind,src={out},dst=/out',
        '--mount', f'type=bind,src={source},dst=/snapshot,readonly',
        '--entrypoint', '/bin/bash', image, '-lc', 'exec sleep infinity']
cid = subprocess.check_output(argv, text=True).strip()
subprocess.run(['docker', 'exec', cid, 'bash', '-c', 'mkdir /src && cp -a /snapshot/. /src/'], check=True)
log = root / 'build-candidate-c753-r4.log'
command = ['docker', 'exec', '-e', 'CARGO_BUILD_JOBS=12', '-e', 'DYNAMO_RUST_MIRROR=rsproxy',
           cid, 'bash', '-c', 'bash /src/container/platforms/build-wheels.sh ascend; rc=$?; printf "%s\\n" "$rc" > /out/build.exit; exit "$rc"']
with log.open('x') as stream:
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                               stderr=subprocess.STDOUT, start_new_session=True)
record = {'source_commit': manifest['commit'], 'source_files': len(manifest['files']),
          'builder_id': cid, 'builder_name': name, 'pid': process.pid, 'argv': command,
          'log': str(log), 'artifacts': str(out), 'source_mode': 'verified snapshot copied into writable isolated builder'}
(root / 'candidate-c753/build-launch.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
