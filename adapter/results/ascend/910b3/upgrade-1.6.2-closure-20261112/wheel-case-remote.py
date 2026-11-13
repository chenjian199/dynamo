#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build Rust/Python wheels through the exact entrypoint and verify offline isolated imports."""
import argparse,hashlib,json,os,shutil,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('spec');a=p.parse_args()
spec=json.loads(Path(a.spec).read_text());source=Path(spec['source']);out=Path(spec['output']);out.mkdir(parents=True,exist_ok=False)
root=Path('/data/dynamo-upgrade-1.6.2-20261111');work=root/'wheel-cases'/spec['container_name'];work.mkdir(parents=True,exist_ok=False)
checkout=work/'source';shutil.copytree(source,checkout)
manifest=json.loads((checkout/'SOURCE.json').read_text())
for name,expected in manifest['files'].items():assert hashlib.sha256((checkout/name).read_bytes()).hexdigest()==expected,name
assert hashlib.sha256((checkout/spec['entrypoint']).read_bytes()).hexdigest()==spec['entrypoint_sha256']
docker='/data/dynamo-m10/docker-bin/docker';env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock'}
record={'spec':spec,'started':time.time(),'commands':[],'source_commit':manifest['commit']};cid=None

def sha(file):
 h=hashlib.sha256()
 with file.open('rb') as stream:
  for chunk in iter(lambda:stream.read(8*1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def call(argv,file,timeout=120,check=True):
 record['commands'].append(argv)
 with (out/file).open('x') as f:
  r=subprocess.run(argv,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 if check and r.returncode:raise RuntimeError(f'{file}: exit {r.returncode}')
 return r

try:
 (out/'artifacts').mkdir()
 argv=[docker,'run','-d','--name',spec['container_name'],'--network','host','--user','0',
       '-e','DYN_PLATFORM=ascend','-e','CARGO_HOME=/build-cache/cargo','-e','RUSTUP_HOME=/build-cache/rustup',
       '-e','CARGO_BUILD_JOBS=12','-e','DYNAMO_RUST_MIRROR=rsproxy',
       '-e','HTTP_PROXY=http://127.0.0.1:17890','-e','HTTPS_PROXY=http://127.0.0.1:17890',
       '-e','http_proxy=http://127.0.0.1:17890','-e','https_proxy=http://127.0.0.1:17890',
       '--mount',f'type=bind,src={root / "cache"},dst=/build-cache',
       '--mount',f'type=bind,src={checkout},dst=/src',
       '--mount',f'type=bind,src={out},dst=/evidence',
       '--mount',f'type=bind,src={out / "artifacts"},dst=/out',
       '--mount','type=bind,src=/usr/local/Ascend/driver,dst=/usr/local/Ascend/driver,readonly',
       spec['image_id'],'sleep','infinity']
 call(argv,'create.txt');cid=(out/'create.txt').read_text().strip();record['container_id']=cid
 (out/'ownership.json').write_text(json.dumps(record,indent=2)+'\n')
 call([docker,'inspect',cid],'container-inspect.json')
 prefix=[docker,'exec',cid,'/opt/dynamo/bin/entrypoint.sh']
 call(prefix+['python','/src/container/platforms/audit.py','snapshot','/evidence/before.json'],'before.log',120)
 command=prefix+['bash','/src/'+spec['entrypoint'],'ascend']
 record['run_command']=command;call(command,'build-wheels.log',6500)
 call(prefix+['python','/src/container/platforms/audit.py','snapshot','/evidence/after.json'],'after.log',120)
 # Offline resolver operates only on the newly built wheelhouse; no index access.
 install_script="""set -euo pipefail
python3 -m venv --system-site-packages /opt/dynamo/closure-verify
/opt/dynamo/closure-verify/bin/python -m pip install --no-index --find-links=/out/wheels -c /out/protected.txt -r /src/container/requirements/platforms/runtime.txt /out/wheels/ai_dynamo_runtime-*.whl /out/wheels/ai_dynamo-*.whl /out/wheels/kvbm-*.whl /out/wheels/gpu_memory_service-*.whl
"""
 call(prefix+['bash','-c',install_script],'offline-install.log',600)
 code="""import importlib,json,importlib.metadata as m,hashlib,zipfile
from pathlib import Path
modules={}
for name,symbol in [('dynamo._core','DistributedRuntime'),('kvbm._core','BlockManager')]:
 module=importlib.import_module(name);obj=getattr(module,symbol);modules[name]={'file':module.__file__,'symbol':obj.__name__,'sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}
wheel_modules={}
for wheel in Path('/out/wheels').glob('*.whl'):
 with zipfile.ZipFile(wheel) as z:
  for n in z.namelist():
   if n.startswith(('dynamo/_core','kvbm/_core')) and n.endswith('.so'):
    wheel_modules[n]={'wheel':wheel.name,'sha256':hashlib.sha256(z.read(n)).hexdigest()}
print(json.dumps({'modules':modules,'wheel_modules':wheel_modules,'versions':{n:m.version(n) for n in ('vllm','vllm-ascend','torch','torch-npu')}}))
"""
 call([docker,'exec',cid,'/opt/dynamo/bin/entrypoint.sh','/opt/dynamo/closure-verify/bin/python','-c',code],'imports.json',180)
 call(prefix+['python','-c','import json,importlib.metadata as m; print(json.dumps({n:m.version(n) for n in ("vllm","vllm-ascend","torch","torch-npu")}))'],'versions.json')
 record['exit_code']=0
except Exception as e:
 record['exit_code']=1;record['error']=repr(e)
finally:
 if cid:record['remove_returncode']=call([docker,'rm','-f',cid],'remove.txt',120,False).returncode
 call([docker,'ps','-aq','--filter','name=^/'+spec['container_name']+'$'],'absence.txt')
 record['remaining_container_ids']=(out/'absence.txt').read_text().split()
 record['finished']=time.time()
 record['files']={str(f.relative_to(out)):sha(f) for f in out.rglob('*') if f.is_file()}
 record['remote_only_files']=[n for n in record['files'] if n.startswith(('artifacts/wheels/','artifacts/native/'))]
 # Large wheels remain in the repository results tree on 52; collect exact small metadata.
 import tarfile
 with tarfile.open(out/'collected.tar.gz','w:gz') as tar:
  for n in record['files']:
   if n not in record['remote_only_files']:tar.add(out/n,arcname=n,recursive=False)
 (out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':record['exit_code'],'error':record.get('error'),'output':str(out)}),flush=True)
raise SystemExit(record['exit_code'] or bool(record['remaining_container_ids']))
