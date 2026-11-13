# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""New isolated candidate build; old source, images, archives and failures stay intact."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
source = root / 'source-64815280cf'
work = root / 'candidate-64815280cf'
receipt = json.loads((root / 'source-64815280cf.tar.json').read_text())
archive = root / receipt['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest() == receipt['sha256']
source.mkdir()
subprocess.run(['tar', '-xzf', str(archive), '-C', str(source)], check=True)
manifest = json.loads((source / 'SOURCE.json').read_text())
assert manifest['commit'] == receipt['commit']
for name, sha in manifest['files'].items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == sha, name
out = work / 'artifacts'
out.mkdir(parents=True)
env = {**os.environ, 'PATH': '/data/dynamo-m10/docker-bin:' + os.environ['PATH'],
       'DOCKER_HOST': 'unix:///run/dynamo-m10-docker.sock',
       'DOCKER_CONFIG': str(root / 'docker-client-direct')}
name = 'heyi-upgrade162-builder-64815280cf'
argv = ['docker', 'run', '-d', '--name', name, '--network', 'host', '--user', '0',
        '--mount', f'type=bind,src={root / "cache"},dst=/build-cache',
        '--mount', f'type=bind,src={out},dst=/out',
        '--mount', f'type=bind,src={source},dst=/snapshot,readonly',
        '--entrypoint', '/bin/bash',
        'sha256:74224d25260decde6abdd26c6ebc383faadbaf792115631bdbe966793742c4db',
        '-lc', 'exec sleep infinity']
cid = subprocess.check_output(argv, env=env, text=True).strip()
subprocess.run(['docker', 'exec', cid, 'bash', '-c', 'mkdir /src && cp -a /snapshot/. /src/'], env=env, check=True)
command = ['docker', 'exec', '-e', 'CARGO_BUILD_JOBS=12', '-e', 'DYNAMO_RUST_MIRROR=rsproxy', cid,
           'bash', '-c', 'bash /src/container/platforms/build-wheels.sh ascend; rc=$?; printf "%s\\n" "$rc" > /out/build.exit; exit "$rc"']
log = work / 'wheels-build.log'
with log.open('x') as stream:
    proc = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=stream,
                            stderr=subprocess.STDOUT, start_new_session=True)
with (work / 'images-build-launch.log').open('x') as stream:
    image_proc = subprocess.Popen(['python3', str(root / 'build-images-64815280cf.py')], env=env,
        stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
record = {'source_commit': manifest['commit'], 'snapshot_sha256': receipt['sha256'],
          'builder_id': cid, 'builder_name': name, 'pid': proc.pid, 'argv': command,
          'log': str(log), 'artifacts': str(out), 'image_pipeline_pid': image_proc.pid,
          'source_mode': 'verified snapshot copied into writable isolated builder'}
(work / 'build-launch.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
