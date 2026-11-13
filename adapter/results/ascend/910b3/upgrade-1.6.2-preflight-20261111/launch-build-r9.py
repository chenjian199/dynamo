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
dev = json.loads((root / 'candidate-49ef/dev-editable-r1/result.json').read_text())
assert dev['passed'] and dev['cleanup']['code'] == 0
previous = json.loads((source / 'SOURCE.json').read_text())
assert previous['commit'] == '49ef509336de2c6573dcffa018b33ed864d0c050'
for name, expected in previous['files'].items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, name
delta = root / 'preliminary/source-1aabe-delta.tar.gz'
assert hashlib.sha256(delta.read_bytes()).hexdigest() == '6c8566d8492acba78276673a1f53bbece3b53cf8b5b75848a46b35114b37ffa9'
subprocess.run(['tar', '-xzf', str(delta), '-C', str(source)], check=True)
manifest = json.loads((source / 'SOURCE.json').read_text())
assert manifest['commit'] == '1aabe435ad56922cf7e12f92f64df2dc39531ccb'
for name, expected in manifest['files'].items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, name
out = root / 'candidate-1aabe/artifacts'
out.mkdir(parents=True, exist_ok=False)
name = 'heyi-upgrade-162-ascend-builder-r9-20261111'
image = 'sha256:74224d25260decde6abdd26c6ebc383faadbaf792115631bdbe966793742c4db'
argv = ['docker', 'run', '-d', '--name', name, '--network', 'host', '--user', '0',
        '--mount', f'type=bind,src={root / "cache"},dst=/build-cache',
        '--mount', f'type=bind,src={out},dst=/out',
        '--mount', f'type=bind,src={source},dst=/snapshot,readonly',
        '--entrypoint', '/bin/bash', image, '-lc', 'exec sleep infinity']
cid = subprocess.check_output(argv, text=True).strip()
subprocess.run(['docker', 'exec', cid, 'bash', '-c', 'mkdir /src && cp -a /snapshot/. /src/'], check=True)
log = root / 'build-candidate-1aabe-r9.log'
command = ['docker', 'exec', '-e', 'CARGO_BUILD_JOBS=12', '-e', 'DYNAMO_RUST_MIRROR=rsproxy',
           cid, 'bash', '-c', 'bash /src/container/platforms/build-wheels.sh ascend; rc=$?; printf "%s\\n" "$rc" > /out/build.exit; exit "$rc"']
with log.open('x') as stream:
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                               stderr=subprocess.STDOUT, start_new_session=True)
record = {'source_commit': manifest['commit'], 'source_files': len(manifest['files']),
          'builder_id': cid, 'builder_name': name, 'pid': process.pid, 'argv': command,
          'log': str(log), 'artifacts': str(out), 'source_mode': 'verified snapshot copied into writable isolated builder'}
(root / 'candidate-1aabe/build-launch.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
