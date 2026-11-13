#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute disk-aware Dynamo routing and matched native workers sequentially."""
import hashlib,importlib.util,json,os,signal,subprocess,sys,time
from contextlib import ExitStack
from pathlib import Path
import requests
spec=json.loads(Path(sys.argv[1]).read_text());root=Path('/harness');entry=root/spec['entrypoint'];out=Path('/evidence/routing')
assert spec['case']=='M06-F01-C03' and hashlib.sha256(entry.read_bytes()).hexdigest()==spec['entrypoint_sha256']
folder=root/'adapter/modules/m05-pd-transfer/common/transfer';sys.path.insert(0,str(root));sys.path.insert(0,str(folder))
loader=importlib.util.spec_from_file_location('closure_routing_run',folder/'run.py');run=importlib.util.module_from_spec(loader);loader.loader.exec_module(run)
original=run.ManagedProcess;launches=[]
class ObservedProcess(original):
 def __init__(self,*args,**kwargs):
  cmd=list(kwargs['command']);label=kwargs['display_name'];env=kwargs['env']
  if label=='routing':
   assert cmd[1]==str(entry)
   kwargs={**kwargs,'command':[cmd[0],'/controller/cache-routing-client.py',*cmd[1:]]}
  launches.append({'label':label,'command':cmd,'environment':{k:env.get(k) for k in ('ASCEND_RT_VISIBLE_DEVICES','DYN_NAMESPACE')}})
  Path('/evidence/routing-launches.json').write_text(json.dumps(launches,indent=2)+'\n')
  super().__init__(*args,**kwargs)
run.ManagedProcess=ObservedProcess
sys.argv=[str(folder/'run.py'),'--platform','ascend','--model',spec['container_model'],'--devices',','.join(map(str,spec['devices'])),
          '--aggregate','--kvbm','--kv-routing','--kv-routing-tier','disk','--output',str(out)]
run.main()
summary=json.loads((out/'summary.json').read_text());assert summary['status']=='passed'
report=summary['routing'];workers=report['workers'];assert len(workers)==2
transcript=[json.loads(x) for x in Path('/evidence/routing-http.jsonl').read_text().splitlines()]
requests_only=[x for x in transcript if x['url'].endswith('/v1/completions')]
assert requests_only and all(x['status']==200 for x in requests_only)
processes=[];native=[];record={'passed':False,'commands':[],'started':time.time()}
try:
 with ExitStack() as stack:
  for index in (0,1):
   launch=next(x for x in launches if x['label']=='aggregate-'+str(index));old=launch['command'];assert old[:3]==[sys.executable,'-m','dynamo.vllm']
   args=[];i=3
   while i<len(old):
    if old[i] in ('--kv-transfer-config','--kv-events-config'):i+=2;continue
    args.append(old[i]);i+=1
   cmd=[sys.executable,'-m','vllm.entrypoints.openai.api_server','--host','127.0.0.1','--port',str(28310+index),*args]
   e={**os.environ,'DYN_PLATFORM':'ascend','ASCEND_RT_VISIBLE_DEVICES':launch['environment']['ASCEND_RT_VISIBLE_DEVICES']}
   f=stack.enter_context(Path('/evidence',f'native-routing-{index}.log').open('x'))
   proc=subprocess.Popen(cmd,env=e,stdout=f,stderr=subprocess.STDOUT,start_new_session=True);processes.append(proc)
   record['commands'].append({'argv':cmd,'device':e['ASCEND_RT_VISIBLE_DEVICES'],'pid':proc.pid})
   deadline=time.monotonic()+900
   while True:
    if proc.poll() is not None:raise RuntimeError('Native route control exited')
    try:
     if requests.get(f'http://127.0.0.1:{28310+index}/health',timeout=5).status_code==200:break
    except requests.RequestException:pass
    if time.monotonic()>deadline:raise TimeoutError('Native route control readiness')
    time.sleep(2)
  for idx,row in enumerate(requests_only):
   # Explicit and automatically selected worker IDs are both observed in the
   # original response. Replay on the corresponding independent native worker.
   actual=row['response']['nvext']['worker_id']['decode_worker_id'];index=workers.index(actual)
   payload={k:v for k,v in row['request'].items() if k!='nvext'};payload['return_token_ids']=True
   response=requests.post(f'http://127.0.0.1:{28310+index}/v1/completions',json=payload,timeout=180);response.raise_for_status()
   native.append({'index':idx,'worker_index':index,'request':payload,'response':response.json()})
  record['passed']=True
finally:
 record['shutdown']=[]
 for proc in reversed(processes):
  if proc.poll() is None:os.killpg(proc.pid,signal.SIGTERM)
  forced=False
  try:proc.wait(timeout=90)
  except subprocess.TimeoutExpired:forced=True;os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=15)
  record['shutdown'].append({'pid':proc.pid,'returncode':proc.returncode,'forced':forced})
 record['responses']=native;record['finished']=time.time();Path('/evidence/native-routing.json').write_text(json.dumps(record,indent=2)+'\n')
assert record['passed'] and not any(x['forced'] for x in record['shutdown'])
