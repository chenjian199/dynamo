# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rebuild the exact frozen H revision for paired comparison, preserving old delivery caches."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
work = root / 'baseline-h'
work.mkdir()
source = work / 'source'
source.mkdir()
archive = root / 'baseline-h-source.tar.gz'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == '62b8c223e12330ecc7cb3a4d060aea89e1500bc9d4f810c087a2df9dac97cb41'
subprocess.run(['tar','-xzf',str(archive),'-C',str(source)],check=True)
manifest=json.loads((source/'SOURCE.json').read_text())
assert manifest['commit']=='bd801c6234aae2827042c8abf268395d4582e5eb'
for name,expected in manifest['files'].items():
    assert hashlib.sha256((source/name).read_bytes()).hexdigest()==expected,name
profile=json.loads((source/'container/platforms/ascend.json').read_text())
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],
     'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(root/'docker-client-direct')}
def run(argv, **kw):
    print(argv,flush=True)
    return subprocess.run(argv,env=env,check=True,**kw)
base=json.loads(subprocess.check_output(['docker','image','inspect',profile['base_image']],env=env))[0]
assert profile['base_image'] in base.get('RepoDigests',[]), 'Expected immutable old vendor base receipt'
cache=work/'cache'
# Copy old cache inputs; never mutate the old M10 delivery/cache.
shutil.copytree('/data/dynamo-m10/cache',cache,symlinks=True)
(cache/'tools').mkdir(exist_ok=True)
protoc=cache/'tools/protoc'
protoc.write_text('#!/bin/sh\nexec /usr/bin/protoc --experimental_allow_proto3_optional "$@"\n');protoc.chmod(0o755)
config=cache/'cargo/config.toml'
if config.exists():
    shutil.copy2(config,config.with_name('config.original.toml'))
    # Use the tested task-owned Cargo networking configuration, not stale proxies.
    shutil.copy2(root/'cache/cargo/config.toml',config)
artifacts=work/'artifacts';artifacts.mkdir()
name='heyi-upgrade-162-old-h-builder-20261111'
argv=['docker','run','-d','--name',name,'--network','host','--user','0',
      '--mount',f'type=bind,src={cache},dst=/build-cache',
      '--mount',f'type=bind,src={source},dst=/snapshot,readonly',
      '--mount',f'type=bind,src={artifacts},dst=/out',
      '--entrypoint','/bin/bash',base['Id'],'-lc','exec sleep infinity']
cid=subprocess.check_output(argv,env=env,text=True).strip()
(work/'ownership.json').write_text(json.dumps({'container_id':cid,'argv':argv,'source_commit':manifest['commit'],'base_id':base['Id']},indent=2)+'\n')
run(['docker','exec',cid,'bash','-c','mkdir /src && cp -a /snapshot/. /src/'])
run(['docker','exec','-e','CARGO_BUILD_JOBS=12','-e','DYNAMO_RUST_MIRROR=rsproxy',
     '-e','PROTOC=/build-cache/tools/protoc',cid,'bash','/src/container/platforms/build-wheels.sh','ascend'],timeout=7200)
(artifacts/'build.exit').write_text('0\n')
context=work/'runtime-context';context.mkdir();dest=context/'artifacts';dest.mkdir()
for n in ('wheels','native','vendor-patches','locks'):shutil.copytree(artifacts/n,dest/n)
for n in ('SOURCE.json','profile.json','base.json','dependencies.json','protected.txt','build-python.txt','build-os.txt','rustc.txt','runtime-features.txt','kvbm-features.txt','native-build.jsonl'):shutil.copy2(artifacts/n,dest/n)
for n in ('audit.py','entrypoint.sh','runtime.Dockerfile'):shutil.copy2(source/'container/platforms'/n,context/n)
sealed={str(p.relative_to(dest)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dest.rglob('*')) if p.is_file()}
(dest/'SHA256.json').write_text(json.dumps(sealed,indent=2)+'\n')
tag='heyi/dynamo:1.5.1-ascend0.23-baseline-bd801c6234-runtime'
run(['docker','build','--network','host','--build-arg','BASE_IMAGE='+base['Id'],'--build-arg','ACCELERATOR=ascend','-f',str(context/'runtime.Dockerfile'),'-t',tag,str(context)],timeout=1800)
info=json.loads(subprocess.check_output(['docker','image','inspect',tag],env=env))[0]
(work/'image.json').write_text(json.dumps({'source_commit':manifest['commit'],'image_id':info['Id'],'tag':tag,'profile':profile},indent=2)+'\n')
