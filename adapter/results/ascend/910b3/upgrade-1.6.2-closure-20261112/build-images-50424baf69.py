# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build sealed runtime and its dev/tools descendants from the owned ARM64 builder."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
source = root / 'source-50424baf69'
work = root / 'candidate-50424baf69'
artifacts = work / 'artifacts'
commit = '50424baf6956bb3bcd87f70ce2fd227d8af03751'
base = 'sha256:74224d25260decde6abdd26c6ebc383faadbaf792115631bdbe966793742c4db'
env = {**os.environ, 'PATH': '/data/dynamo-m10/docker-bin:' + os.environ['PATH'],
       'DOCKER_HOST': 'unix:///run/dynamo-m10-docker.sock',
       'DOCKER_CONFIG': str(root / 'docker-client-direct')}

def run(args, log, extra=None):
    with (work / log).open('x') as out:
        result = subprocess.run(args, env={**env, **(extra or {})}, cwd=source,
                                stdout=out, stderr=subprocess.STDOUT, timeout=5400)
    (work / (log + '.exit')).write_text(str(result.returncode) + '\n')
    if result.returncode:
        raise RuntimeError(f'{log} failed: {result.returncode}')

def image_id(tag):
    return json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag], env=env))[0]['Id']

def main():
    deadline = time.monotonic() + 10800
    while not (artifacts / 'build.exit').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('Wheel builder has not completed')
        time.sleep(10)
    assert (artifacts / 'build.exit').read_text().strip() == '0'
    manifest = json.loads((source / 'SOURCE.json').read_text())
    assert manifest['commit'] == commit
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, name
    assert json.loads((artifacts / 'SOURCE.json').read_text()) == manifest
    context = work / 'runtime-context'
    context.mkdir()
    dest = context / 'artifacts'
    dest.mkdir()
    for name in ('wheels', 'native', 'vendor-patches', 'locks'):
        shutil.copytree(artifacts / name, dest / name)
    for name in ('SOURCE.json', 'profile.json', 'base.json', 'dependencies.json', 'protected.txt',
                 'build-python.txt', 'build-os.txt', 'rustc.txt', 'runtime-features.txt',
                 'kvbm-features.txt', 'native-build.jsonl'):
        shutil.copy2(artifacts / name, dest / name)
    for name in ('audit.py', 'entrypoint.sh', 'runtime.Dockerfile'):
        shutil.copy2(source / 'container/platforms' / name, context / name)
    sealed = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(dest.rglob('*')) if p.is_file()}
    (dest / 'SHA256.json').write_text(json.dumps(sealed, indent=2) + '\n')
    assert image_id(base) == base
    tag = 'heyi/dynamo:1.6.2-ascend0.30-50424baf69-runtime'
    run(['docker', 'build', '--network', 'host', '--build-arg', 'BASE_IMAGE=' + base,
         '--build-arg', 'ACCELERATOR=ascend', '-f', str(context / 'runtime.Dockerfile'),
         '-t', tag, str(context)], 'runtime-build.log', {'DOCKER_BUILDKIT': '0'})
    runtime = image_id(tag)
    record = {'source_commit': commit, 'base_image_id': base, 'runtime': {'tag': tag, 'id': runtime}}
    (work / 'images.json').write_text(json.dumps(record, indent=2) + '\n')
    tools = work / 'tools-context'
    subprocess.run(['python3', str(source / 'adapter/modules/m10-validation/common/images/prepare-tools.py'),
        '--source', str(source), '--platform', 'ascend', '--kubectl', '/data/dynamo-m10/ascend-tools-context/kubectl',
        '--override-bin', str(root / 'tools-inputs/dgd-apply-overrides'), '--aisimulate-wheel',
        str(root / 'tools-inputs/aisimulate-0.13.0-cp311-abi3-manylinux_2_28_aarch64.whl'), '--output', str(tools)], check=True)
    # Descendant builds are independent; retain process ownership and exit codes.
    jobs = {
        'tools': ['docker', 'buildx', 'build', '--load', '--network', 'host', '--progress', 'plain',
                  '--build-arg', 'RUNTIME_IMAGE=' + tag, '--build-arg', 'PIP_INDEX_URL=https://pypi.org/simple', '--add-host', 'pypi.org:' + socket.getaddrinfo('pypi.org',443,socket.AF_INET)[0][4][0], '--add-host', 'files.pythonhosted.org:' + socket.getaddrinfo('files.pythonhosted.org',443,socket.AF_INET)[0][4][0], '-f', str(tools / 'tools.Dockerfile'),
                  '-t', tag.replace('-runtime', '-tools'), str(tools)],
        'dev': ['python3', str(source / 'container/platform-build.py'), '--device', 'ascend', '--target', 'dev',
                '--runtime-image', runtime, '--tag', tag.replace('-runtime', '-dev'),
                '--output', str(work / 'dev-build'), '--rust-mirror', 'rsproxy'],
    }
    running = {}
    for kind, argv in jobs.items():
        stream = (work / f'{kind}-build.log').open('x')
        running[kind] = (subprocess.Popen(argv, cwd=source, env=env, stdout=stream,
                                        stderr=subprocess.STDOUT), stream)
    (work / 'descendant-processes.json').write_text(json.dumps(
        {k: {'pid': p.pid, 'argv': jobs[k]} for k, (p, _) in running.items()}, indent=2) + '\n')
    for kind, (process, stream) in running.items():
        try:
            rc = process.wait(timeout=5400)
        except subprocess.TimeoutExpired:
            process.terminate()
            rc = process.wait(timeout=30)
        stream.close()
        (work / f'{kind}-build.exit').write_text(str(rc) + '\n')
        record[kind] = {'exit_code': rc}
        if rc == 0:
            child_tag = tag.replace('-runtime', '-' + kind)
            record[kind].update({'tag': child_tag, 'id': image_id(child_tag)})
        (work / 'images.json').write_text(json.dumps(record, indent=2) + '\n')
    assert all(record[k]['exit_code'] == 0 for k in jobs), record

if __name__ == '__main__':
    main()
