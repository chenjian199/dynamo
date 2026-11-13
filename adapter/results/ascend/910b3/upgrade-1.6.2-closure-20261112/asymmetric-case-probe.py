#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run existing PD harness, adding positive handoff capture before its normal idle check."""
import hashlib,importlib.util,json,os,subprocess,sys,threading,time
from pathlib import Path
spec=json.loads(Path(sys.argv[1]).read_text());out=Path('/evidence/pd');root=Path('/harness')
folder=root/'adapter/modules/m05-pd-transfer/common/transfer';sys.path.insert(0,str(folder));sys.path.insert(0,str(root))
entry=root/spec['entrypoint'];assert hashlib.sha256(entry.read_bytes()).hexdigest()==spec['entrypoint_sha256']
loader=importlib.util.spec_from_file_location('closure_pd_run',folder/'run.py')
module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module)
original_process=module.ManagedProcess;original_idle=module.collect_idle_metrics
real_post=module.requests.post;write_lock=threading.Lock()
def observed_post(url,*args,**kwargs):
 started=time.time_ns();response=real_post(url,*args,**kwargs)
 row={'started_ns':started,'finished_ns':time.time_ns(),'url':url,'request':kwargs.get('json'),'stream':bool(kwargs.get('stream')),'status':response.status_code}
 if not kwargs.get('stream'):
  try:row['response']=response.json()
  except ValueError:row['body']=response.text
 with write_lock:
  with (out/'http-responses.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
 return response
module.requests.post=observed_post
seen=[];commands=[];environment=None;captured=False

class ObservedProcess(original_process):
 def __init__(self,*args,**kw):
  global environment
  e=kw.get('env',{});environment=e
  if spec['case']=='M05-F04-C01':e['VLLM_LOGGING_LEVEL']='DEBUG'
  seen.append({'argv':kw.get('command'),'display_name':kw.get('display_name'),'started_ns':time.time_ns(),
               'environment':{k:e.get(k) for k in ('DYN_NAMESPACE','DYN_DISCOVERY_BACKEND','DYN_FILE_KV','DYN_SYSTEM_PORT','ASCEND_RT_VISIBLE_DEVICES')}})
  # run.py creates output before constructing any ManagedProcess.
  (out/'observed-launches.json').write_text(json.dumps(seen,indent=2)+'\n')
  self.closure_label=kw.get('display_name')
  super().__init__(*args,**kw)
 def __exit__(self,*args):
  try:return super().__exit__(*args)
  finally:
   with (out/'process-exits.jsonl').open('a') as f:f.write(json.dumps({'label':self.closure_label,'finished_ns':time.time_ns(),'returncode':self.proc.returncode if self.proc else None})+'\n')

def execute(path,args,label):
 argv=[sys.executable,str(path),*args];commands.append({'argv':argv,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 (out/'probe-commands.json').write_text(json.dumps(commands,indent=2)+'\n')
 with (out/(label+'.log')).open('x') as f:subprocess.run(argv,env={**environment,'DYN_PLATFORM':'cpu','DYN_SYSTEM_PORT':'0'},stdout=f,stderr=subprocess.STDOUT,check=True,timeout=180)

def idle(output,ports,roles,suffix):
 global captured
 if suffix=='normal' and not captured:
  captured=True
  ns=environment['DYN_NAMESPACE']
  execute(folder/'handoff.py',['--namespace',ns,'--case','valid','--output',str(out/'valid-handoff.json')],'valid-handoff')
  if spec['case']=='M05-F04-C01':
   execute(folder/'topology.py',['--namespace',ns,'--prefill-workers','1','--decode-workers','1','--output',str(out/'explicit-topology.json')],'explicit-topology')
 return original_idle(output,ports,roles,suffix)

module.ManagedProcess=ObservedProcess;module.collect_idle_metrics=idle
cid=spec['case'];top=spec['topology']
argv=['--platform','ascend','--devices',','.join(map(str,spec['devices'])),'--model',spec['container_model'],'--output',str(out),
      '--prefill-workers',str(top.get('prefill_replicas',1)),'--decode-workers',str(top.get('decode_replicas',1)),
      '--prefill-tp',str(top.get('prefill_tp',1)),'--decode-tp',str(top.get('decode_tp',1))]
if cid=='M05-F01-C01':argv+=['--handoff-case','mismatch']
elif cid=='M05-F06-C01':argv+=['--soak-seconds','60','--soak-requests','96','--cancel']
elif cid=='M05-F06-C02':argv+=['--soak-seconds','60','--soak-requests','320']
elif cid=='M05-F06-C03':argv+=['--soak-seconds','1800','--soak-requests','1000']
assert cid in ('M05-F01-C01','M05-F03-C01','M05-F04-C01','M05-F06-C01','M05-F06-C02','M05-F06-C03')
if cid.startswith('M05-F06-'):
 import soak
 real_soak=soak.run_soak
 def observed_soak(*args,**kwargs):
  (out/'entry-invocation.json').write_text(json.dumps({'entrypoint':str(entry),'sha256':hashlib.sha256(entry.read_bytes()).hexdigest(),'operation':'run_soak','seconds':args[0].soak_seconds,'minimum_requests':args[0].soak_requests},indent=2)+'\n')
  return real_soak(*args,**kwargs)
 soak.run_soak=observed_soak
sys.argv=[str(folder/'run.py'),*argv]
module.main()
if not cid.startswith('M05-F06-'):
 calls=commands+[{'argv':x['argv']} for x in seen]
 assert any(str(entry) in c['argv'] for c in calls)
 (out/'entry-invocation.json').write_text(json.dumps({'entrypoint':str(entry),'sha256':hashlib.sha256(entry.read_bytes()).hexdigest(),'calls':[c for c in calls if str(entry) in c['argv']]},indent=2)+'\n')
