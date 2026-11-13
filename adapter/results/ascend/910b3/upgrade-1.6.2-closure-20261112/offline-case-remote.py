#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify sealed wheel dependencies with networking disabled in an owned container."""
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('spec');a=p.parse_args()
spec=json.loads(Path(a.spec).read_text());source=Path(spec['source']);out=Path(spec['output']);out.mkdir(parents=True,exist_ok=False)
docker='/data/dynamo-m10/docker-bin/docker';env={**os.environ,'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock'}
record={'spec':spec,'started':time.time(),'commands':[]};cid=None

def call(argv,file,timeout=120,check=True):
 record['commands'].append(argv)
 with (out/file).open('x') as f:r=subprocess.run(argv,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 if check and r.returncode:raise RuntimeError(f'{file}: exit {r.returncode}')
 return r

try:
 entry=source/spec['entrypoint'];assert hashlib.sha256(entry.read_bytes()).hexdigest()==spec['entrypoint_sha256']
 argv=[docker,'run','-d','--name',spec['container_name'],'--network','none','--user','0',
       '-e','DYN_PLATFORM=ascend','-e','PIP_NO_INDEX=1','-e','HF_HUB_OFFLINE=1',
       '--mount','type=bind,src=/usr/local/Ascend/driver,dst=/usr/local/Ascend/driver,readonly',spec['image_id'],'sleep','infinity']
 call(argv,'create.txt');cid=(out/'create.txt').read_text().strip();record['container_id']=cid
 (out/'ownership.json').write_text(json.dumps(record,indent=2)+'\n')
 call([docker,'inspect',cid],'container-inspect.json')
 # Copy only the exact audit entrypoint. No product source is mounted or imported.
 call([docker,'cp',str(entry),cid+':/tmp/closure-audit.py'],'copy-entrypoint.txt')
 prefix=[docker,'exec',cid,'/opt/dynamo/bin/entrypoint.sh']
 code="import hashlib,json,pathlib; p=pathlib.Path('/tmp/closure-audit.py'); print(json.dumps({'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))"
 call(prefix+['python','-c',code],'entrypoint.json')
 assert json.loads((out/'entrypoint.json').read_text())['sha256']==spec['entrypoint_sha256']
 call(prefix+['python','/tmp/closure-audit.py','verify','/opt/dynamo/artifacts'],'artifact-verification.log',300)
 call(prefix+['python','/tmp/closure-audit.py','snapshot','/tmp/offline-before.json'],'before.log')
 script='''set -euo pipefail
python3 -m venv --system-site-packages /opt/dynamo/offline-verify
/opt/dynamo/offline-verify/bin/python -m pip install --no-index --find-links=/opt/dynamo/artifacts/wheels -c /opt/dynamo/artifacts/protected.txt /opt/dynamo/artifacts/wheels/ai_dynamo_runtime-*.whl /opt/dynamo/artifacts/wheels/ai_dynamo-*.whl /opt/dynamo/artifacts/wheels/kvbm-*.whl /opt/dynamo/artifacts/wheels/gpu_memory_service-*.whl
'''
 call(prefix+['bash','-c',script],'offline-install.log',600)
 code="""import hashlib,importlib,json,importlib.metadata as m,pathlib,zipfile
mods={}
for name in ('dynamo._core','kvbm._core','dynamo.common.platform','gpu_memory_service'):
 module=importlib.import_module(name);p=pathlib.Path(module.__file__).resolve()
 assert str(p).startswith('/opt/dynamo/offline-verify/')
 mods[name]={'file':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
print(json.dumps({'imports':mods,'versions':{n:m.version(n) for n in ('vllm','vllm-ascend','torch','torch-npu')}}))
"""
 call([docker,'exec',cid,'/opt/dynamo/bin/entrypoint.sh','/opt/dynamo/offline-verify/bin/python','-c',code],'imports.json',180)
 call(prefix+['python','/tmp/closure-audit.py','snapshot','/tmp/offline-after.json'],'after.log')
 for file,path in [('before.json','/tmp/offline-before.json'),('after.json','/tmp/offline-after.json'),('embedded-source.json','/opt/dynamo/artifacts/SOURCE.json'),('profile.json','/opt/dynamo/artifacts/profile.json'),('sealed-artifacts.json','/opt/dynamo/artifacts/SHA256.json')]:
  call([docker,'cp',cid+':'+path,str(out/file)],file+'.copy.log')
 record['exit_code']=0
except Exception as e:record['exit_code']=1;record['error']=repr(e)
finally:
 if cid:record['remove_returncode']=call([docker,'rm','-f',cid],'remove.txt',120,False).returncode
 call([docker,'ps','-aq','--filter','name=^/'+spec['container_name']+'$'],'absence.txt')
 record['remaining_container_ids']=(out/'absence.txt').read_text().split();record['finished']=time.time()
 record['files']={str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in out.rglob('*') if f.is_file()}
 (out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':record['exit_code'],'error':record.get('error'),'output':str(out)}),flush=True)
raise SystemExit(record['exit_code'] or bool(record['remaining_container_ids']))
