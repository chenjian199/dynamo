#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify runtime, dev ancestry and independent tools interpreter using actual images."""
import argparse,hashlib,json,os,shutil,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('spec');a=p.parse_args()
spec=json.loads(Path(a.spec).read_text());source=Path(spec['source']);out=Path(spec['output']);out.mkdir(parents=True,exist_ok=False)
root=Path('/data/dynamo-upgrade-1.6.2-20261111');work=root/'editable-cases'/spec['container_name'];work.mkdir(parents=True,exist_ok=False)
checkout=work/'source';shutil.copytree(source,checkout)
manifest=json.loads((checkout/'SOURCE.json').read_text())
for name,expected in manifest['files'].items():assert hashlib.sha256((checkout/name).read_bytes()).hexdigest()==expected,name
assert hashlib.sha256((checkout/spec['entrypoint']).read_bytes()).hexdigest()==spec['entrypoint_sha256']
docker='/data/dynamo-m10/docker-bin/docker';env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock'}
record={'spec':spec,'started':time.time(),'commands':[],'source_commit':manifest['commit']};cid=None;extra_containers={}

def call(argv,file,timeout=120,check=True):
 record['commands'].append(argv)
 with (out/file).open('x') as f:
  r=subprocess.run(argv,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 if check and r.returncode:raise RuntimeError(f'{file}: exit {r.returncode}')
 return r

try:
 argv=[docker,'run','-d','--name',spec['container_name'],'--network','host','--user','0',
       '-e','DYN_PLATFORM=ascend','-e','CARGO_HOME=/build-cache/cargo','-e','RUSTUP_HOME=/build-cache/rustup',
       '-e','CARGO_TARGET_DIR=/build-cache/targets/ascend/closure-'+manifest['commit'][:10]+'-dev','-e','CARGO_BUILD_JOBS=12',
       '--mount',f'type=bind,src={root / "cache"},dst=/build-cache',
       '--mount',f'type=bind,src={checkout},dst=/workspace/dynamo',
       '--mount',f'type=bind,src={out},dst=/evidence',
       '--mount','type=bind,src=/usr/local/Ascend/driver,dst=/usr/local/Ascend/driver,readonly',
       spec['image_id'],'sleep','infinity']
 call(argv,'create.txt');cid=(out/'create.txt').read_text().strip();record['container_id']=cid
 (out/'ownership.json').write_text(json.dumps(record,indent=2)+'\n')
 call([docker,'inspect',cid],'container-inspect.json')
 prefix=[docker,'exec',cid,'/opt/dynamo/bin/entrypoint.sh']
 call(prefix+['bash','/opt/dynamo/bin/dev-editable.sh','all'],'editable.log',6500)
 # The contract entrypoint independently checks image ancestry, tools, source
 # imports, Rust classes, editable metadata, and unchanged runtime packages.
 command=['python3',str(checkout/spec['entrypoint']),'--container',cid,
          '--build',str(root/('candidate-'+manifest['commit'][:10])/'dev-build'),'--output',str(out/'dev-validation.json')]
 record['run_command']=command;call(command,'validate.log',300)
 call(prefix+['python','-c','import json,importlib.metadata as m; print(json.dumps({n:m.version(n) for n in ("vllm","vllm-ascend","torch","torch-npu")}))'],'versions.json')
 # Probe immutable runtime and tools independently, without product-source mounts.
 for role in ['runtime','tools']:
  image=spec['images'][role]
  command=[docker,'run','-d','--name',spec['container_name']+'-'+role,'--network','none','--user','0',
    '--mount','type=bind,src=/usr/local/Ascend/driver,dst=/usr/local/Ascend/driver,readonly',image['config_id'],'sleep','infinity']
  call(command,role+'-create.txt');other=(out/(role+'-create.txt')).read_text().strip();extra_containers[role]=other
  call([docker,'inspect',other],role+'-inspect.json')
  checkfile=source/'adapter/modules/m10-validation/common/regression/check-installed.py'
  call([docker,'cp',str(checkfile),other+':/tmp/check-installed.py'],role+'-copy.txt')
  call([docker,'exec','-e','DYN_PLATFORM=ascend',other,'/opt/dynamo/venv/bin/python','/tmp/check-installed.py','--role','runtime'],role+'-installed.json',180)
  if role=='tools':
   code="""import importlib,json,sys,pathlib,importlib.metadata as m
modules={}
for name in ('dynamo.profiler','dynamo.planner','aisimulate','aiperf'):
 module=importlib.import_module(name);modules[name]=str(pathlib.Path(module.__file__).resolve())
print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'paths':sys.path,'modules':modules,'versions':{n:m.version(n) for n in ('ai-dynamo','aisimulate','aiperf')}}))
"""
   call([docker,'exec',other,'/opt/dynamo/tools-venv/bin/python','-c',code],'tools-isolation.json',180)
   for module in ['dynamo.profiler','dynamo.planner']:
    call([docker,'exec',other,'/opt/dynamo/tools-venv/bin/python','-m',module,'--help'],module+'-help.txt',180)
   call([docker,'exec',other,'/opt/dynamo/tools-venv/bin/python','-m','pip','check'],'tools-pip-check.txt',120)
   for name in ['before','after']:
    call([docker,'cp',other+':/opt/dynamo/tools-build/'+name+'.json',str(out/('tools-runtime-'+name+'.json'))],name+'-copy.txt')
 record['exit_code']=0
except Exception as e:
 record['exit_code']=1;record['error']=repr(e)
finally:
 record['extra_cleanup']={}
 for role,other in extra_containers.items():
  result=call([docker,'rm','-f',other],role+'-remove.txt',120,False)
  call([docker,'ps','-aq','--filter','name=^/'+spec['container_name']+'-'+role+'$'],role+'-absence.txt')
  record['extra_cleanup'][role]={'returncode':result.returncode,'remaining_ids':(out/(role+'-absence.txt')).read_text().split()}
 if cid:record['remove_returncode']=call([docker,'rm','-f',cid],'remove.txt',120,False).returncode
 call([docker,'ps','-aq','--filter','name=^/'+spec['container_name']+'$'],'absence.txt')
 record['remaining_container_ids']=(out/'absence.txt').read_text().split()
 record['finished']=time.time()
 record['files']={str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in out.rglob('*') if f.is_file()}
 (out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':record['exit_code'],'error':record.get('error'),'output':str(out)}),flush=True)
raise SystemExit(record['exit_code'] or bool(record['remaining_container_ids']))
