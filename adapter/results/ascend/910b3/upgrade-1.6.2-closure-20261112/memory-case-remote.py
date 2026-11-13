#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute the exact memory run.sh entrypoint with image-bound provenance and cleanup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

p=argparse.ArgumentParser();p.add_argument('spec');a=p.parse_args()
spec=json.loads(Path(a.spec).read_text());source=Path(spec['source']);out=Path(spec['output']);out.mkdir(parents=True,exist_ok=False)
docker='/data/dynamo-m10/docker-bin/docker';env={**os.environ,'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock'}
record={'spec':spec,'started':time.time(),'commands':[]};cid=None

def call(argv,file,timeout=60,check=True):
 record['commands'].append(argv)
 r=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=timeout)
 (out/file).write_text(r.stdout);(out/(file+'.stderr')).write_text(r.stderr)
 if check and r.returncode:raise RuntimeError(f'{file}: exit {r.returncode}')
 return r

try:
 script=source/spec['entrypoint'];assert hashlib.sha256(script.read_bytes()).hexdigest()==spec['entrypoint_sha256']
 before=call(['npu-smi','info'],'npu-before.txt').stdout
 for d in spec['devices']:assert f'No running processes found in NPU {d}' in before
 for i,model in enumerate(spec.get('models',[])):
  call(['python3',str(source/'adapter/workflow/assets.py'),json.dumps(model)],f'model-{i}.json',1800)
 argv=[docker,'run','-d','--name',spec['container_name'],'--network','host','--user','0',
       '-e','CARGO_HOME=/build-cache/cargo','-e','RUSTUP_HOME=/opt/dynamo/rustup',
       '-e','CARGO_BIN=/opt/dynamo/cargo/bin/cargo',
       '-e','CARGO_TARGET_DIR=/build-cache/targets/ascend/m04-'+json.loads((source/'SOURCE.json').read_text())['commit'][:10],
       '-e','CARGO_BUILD_JOBS=12',
       '--mount','type=bind,src=/data/dynamo-upgrade-1.6.2-20261111/cache,dst=/build-cache',
       '--mount',f'type=bind,src={source},dst=/harness,readonly',
       '--mount',f'type=bind,src={out},dst=/evidence']
 for d in ['davinci'+str(i) for i in spec['devices']]+['davinci_manager','devmm_svm','hisi_hdc']:
  argv+=['--device','/dev/'+d]
 for d in ['/usr/local/Ascend/driver','/usr/local/dcmi','/usr/local/sbin/npu-smi']:
  argv+=['--mount',f'type=bind,src={d},dst={d},readonly']
 argv+=[spec['image_id'],'sleep','infinity']
 cid=call(argv,'create.txt').stdout.strip();record['container_id']=cid
 (out/'ownership.json').write_text(json.dumps(record,indent=2)+'\n')
 call([docker,'inspect',cid],'container-inspect.json')
 prefix=[docker,'exec',cid,'/opt/dynamo/bin/entrypoint.sh']
 call(prefix+['python','/harness/adapter/modules/m10-validation/common/regression/check-installed.py','--role','runtime'],'installed.json',120)
 call(prefix+['python','-c','import json,importlib.metadata as m; print(json.dumps({n:m.version(n) for n in ("vllm","vllm-ascend","torch","torch-npu")}))'],'versions.json')
 command=prefix+['bash','/harness/'+spec['entrypoint'],'ascend','/evidence/memory']
 record['run_command']=command
 call(command,'memory.log',3600)
 record['exit_code']=0
except Exception as e:
 record['exit_code']=1;record['error']=repr(e)
finally:
 if cid:record['remove_returncode']=call([docker,'rm','-f',cid],'remove.txt',120,False).returncode
 record['remaining_container_ids']=call([docker,'ps','-aq','--filter','name=^/'+spec['container_name']+'$'],'absence.txt').stdout.split()
 call(['npu-smi','info'],'npu-after.txt')
 record['finished']=time.time()
 record['files']={str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in out.rglob('*') if f.is_file()}
 (out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':record['exit_code'],'error':record.get('error'),'output':str(out)}),flush=True)
raise SystemExit(record['exit_code'] or bool(record['remaining_container_ids']))
