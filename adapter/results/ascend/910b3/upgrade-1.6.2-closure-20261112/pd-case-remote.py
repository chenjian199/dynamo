#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run installed PD contract, topology and lifecycle harnesses with exact identities."""
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('spec');a=p.parse_args()
spec=json.loads(Path(a.spec).read_text());source=Path(spec['source']);out=Path(spec['output']);out.mkdir(parents=True,exist_ok=False)
docker='/data/dynamo-m10/docker-bin/docker';env={**os.environ,'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock'}
record={'spec':dict(spec),'started':time.time(),'commands':[]};cid=None

def call(argv,file,timeout=60,check=True):
 record['commands'].append(argv)
 with (out/file).open('x') as f:r=subprocess.run(argv,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 if check and r.returncode:raise RuntimeError(f'{file}: exit {r.returncode}')
 return r

try:
 script=source/spec['entrypoint'];assert hashlib.sha256(script.read_bytes()).hexdigest()==spec['entrypoint_sha256']
 call(['npu-smi','info'],'npu-before.txt');before=(out/'npu-before.txt').read_text()
 for d in spec['devices']:assert f'No running processes found in NPU {d}' in before
 for i,model in enumerate(spec['models']):call(['python3',str(source/'adapter/workflow/assets.py'),json.dumps(model)],f'model-{i}.json',1800)
 model=spec['models'][0];top=spec['topology'];spec['container_model']='/models/'+model['id']
 local_spec=out/'executed-spec.json';local_spec.write_text(json.dumps(spec,indent=2)+'\n')
 argv=[docker,'run','-d','--name',spec['container_name'],'--network','host','--shm-size','16g','--user','0',
       '-e','DYN_PLATFORM=ascend','-e','PLATFORM=ascend','-e','HF_HUB_OFFLINE=1','-e','OMP_NUM_THREADS=4',
       '-e','PYTHONUNBUFFERED=1','-e','TP='+str(top.get('tp',1)),'-e','DP='+str(top.get('dp',1)),
       '-e','DEVICES='+','.join(map(str,spec['devices'])),'-e','ASCEND_RT_VISIBLE_DEVICES='+','.join(map(str,spec['devices'])),
       '-e','MODEL_PATH='+spec['container_model'],'-e','_PROFILE_OVERRIDE_VLLM_KV_CACHE_BYTES=1073741824',
       '--mount',f'type=bind,src={source},dst=/harness,readonly',
       '--mount',f'type=bind,src={out},dst=/evidence',
       '--mount',f'type=bind,src={model["path"]},dst={spec["container_model"]},readonly',
       '--mount',f'type=bind,src={Path(a.spec).parent},dst=/controller,readonly']
 for d in ['davinci'+str(i) for i in spec['devices']]+['davinci_manager','devmm_svm','hisi_hdc']:argv+=['--device','/dev/'+d]
 for d in ['/usr/local/Ascend/driver','/usr/local/dcmi','/usr/local/sbin/npu-smi']:argv+=['--mount',f'type=bind,src={d},dst={d},readonly']
 argv+=[spec['image_id'],'sleep','infinity'];call(argv,'create.txt');cid=(out/'create.txt').read_text().strip();record['container_id']=cid
 (out/'ownership.json').write_text(json.dumps(record,indent=2)+'\n')
 call([docker,'inspect',cid],'container-inspect.json')
 prefix=[docker,'exec',cid,'/opt/dynamo/bin/entrypoint.sh']
 call(prefix+['python','/harness/adapter/modules/m10-validation/common/regression/check-installed.py','--role','runtime'],'installed.json',120)
 call(prefix+['python','-c','import json,importlib.metadata as m; print(json.dumps({n:m.version(n) for n in ("vllm","vllm-ascend","torch","torch-npu")}))'],'versions.json')
 command=prefix+['python','/controller/pd-case-probe.py','/evidence/executed-spec.json']
 record['run_command']=command;call(command,'pd.log',6500)
 summary=json.loads((out/'pd/summary.json').read_text());assert summary['status']=='passed'
 record['exit_code']=0
except Exception as e:record['exit_code']=1;record['error']=repr(e)
finally:
 if cid:record['remove_returncode']=call([docker,'rm','-f',cid],'remove.txt',120,False).returncode
 call([docker,'ps','-aq','--filter','name=^/'+spec['container_name']+'$'],'absence.txt')
 record['remaining_container_ids']=(out/'absence.txt').read_text().split();call(['npu-smi','info'],'npu-after.txt')
 record['finished']=time.time();record['files']={str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in out.rglob('*') if f.is_file()}
 (out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':record['exit_code'],'error':record.get('error'),'output':str(out)}),flush=True)
raise SystemExit(record['exit_code'] or bool(record['remaining_container_ids']))
