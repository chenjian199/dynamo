#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded diagnostic; successful execution is not standard feature qualification."""
import json,os,subprocess,time
from pathlib import Path
r=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112')
out=r/'deepseek-rank-diagnostic-2a239c2b50-controller';out.mkdir(exist_ok=False)
k=['kubectl','-n','heyi-upgrade162'];env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'};name='closure-deepseek-rank-2a239c2b50';uid=None
record={'started':time.time(),'purpose':'Diagnose M03 TP8/EP with prefix cache and KV events; not a formal qualification','passed':False}
def call(args):return subprocess.run(args,env=env,capture_output=True,text=True,timeout=60)
try:
 p=call(k+['get','pod',name,'--ignore-not-found','-o','name']);p.check_returncode();assert not p.stdout.strip()
 pods=json.loads(call(['kubectl','get','pods','-A','-o','json']).stdout)
 occupied=[p['metadata']['name'] for p in pods['items'] if p['spec'].get('nodeName')=='master' and p['status']['phase'] not in ('Succeeded','Failed') and any(int(c.get('resources',{}).get('limits',{}).get('huawei.com/Ascend910',0))>0 for c in p['spec']['containers'])]
 assert not occupied,occupied
 p=call(k+['create','-f',str(r/'deepseek-rank-diagnostic-2a239c2b50.json')]);(out/'create.log').write_text(p.stdout+p.stderr);p.check_returncode()
 deadline=time.monotonic()+2500
 while True:
  p=call(k+['get','pod',name,'-o','json']);p.check_returncode();pod=json.loads(p.stdout);uid=pod['metadata']['uid']
  (out/'pod.json').write_text(json.dumps(pod,indent=2)+'\n');record['uid']=uid;(out/'ownership.json').write_text(json.dumps(record,indent=2)+'\n')
  if pod['status']['phase'] in ('Succeeded','Failed'):
   record['phase']=pod['status']['phase'];record['passed']=pod['status']['phase']=='Succeeded';break
  if time.monotonic()>deadline:raise TimeoutError('Bounded DeepSeek diagnostic')
  time.sleep(15)
except Exception as e:record['error']=repr(e)
finally:
 if uid:
  logs=call(k+['logs',name]);(out/'pod.log').write_text(logs.stdout+logs.stderr)
  body=out/'delete.json';body.write_text(json.dumps({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':uid}}))
  result=call(k+['delete','--raw','/api/v1/namespaces/heyi-upgrade162/pods/'+name,'-f',str(body)]);(out/'delete.log').write_text(result.stdout+result.stderr);record['delete_code']=result.returncode
  deadline=time.monotonic()+180
  while time.monotonic()<deadline:
   p=call(k+['get','pod',name,'--ignore-not-found','-o','name']);p.check_returncode()
   if not p.stdout.strip():record['cleanup_complete']=True;break
   time.sleep(3)
 record['finished']=time.time();(out/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2),flush=True)
