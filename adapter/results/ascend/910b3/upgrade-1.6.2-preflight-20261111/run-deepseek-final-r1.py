# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Wait for verified import, deploy one owned DGD, replay API control and clean up."""
import json,os,subprocess,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');out=r/'dynamo-deepseek-api-final-r1';out.mkdir();env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'}
context=subprocess.check_output(['kubectl','config','current-context'],env=env,text=True).strip();k=['kubectl','--context',context,'-n','heyi-upgrade162'];name='upgrade-deepseek-final-r1';record={'context':context,'started':time.time(),'passed':False,'source_commit':'1aabe435ad56922cf7e12f92f64df2dc39531ccb'};uid=None
try:
 deadline=time.monotonic()+1800
 receipt=r/'distribution/runtime-1aabe-import.json'
 while not receipt.exists():
  if time.monotonic()>deadline:raise TimeoutError('Verified image import not ready')
  time.sleep(10)
 assert json.loads(receipt.read_text())['passed']
 manifest=r/'deepseek-final-r1.json'
 subprocess.run(k+['apply','--dry-run=server','-f',str(manifest)],env=env,check=True)
 subprocess.run(k+['apply','-f',str(manifest)],env=env,check=True)
 d=json.loads(subprocess.check_output(k+['get','dgd',name,'-o','json'],env=env));uid=d['metadata']['uid'];record['uid']=uid;(out/'ownership.json').write_text(json.dumps(record,indent=2))
 deadline=time.monotonic()+1500
 while True:
  d=json.loads(subprocess.check_output(k+['get','dgd',name,'-o','json'],env=env));(out/'dgd-current.json').write_text(json.dumps(d,indent=2))
  if any(c['type']=='Ready' and c['status']=='True' for c in d.get('status',{}).get('conditions',[])):break
  if time.monotonic()>deadline:raise TimeoutError('DGD readiness')
  time.sleep(10)
 pods=json.loads(subprocess.check_output(k+['get','pods','-o','json'],env=env));selected=[p for p in pods['items'] if p['metadata']['name'].startswith(name+'-')];(out/'pods.json').write_text(json.dumps(selected,indent=2))
 frontend=next(p for p in selected if p['metadata']['name'].startswith(name+'-frontend-'));url='http://'+frontend['status']['podIP']+':8000'
 subprocess.run(['python3',str(r/'deepseek-api-paired-client.py'),'--url',url,'--output',str(out/'api')],check=True,timeout=2100)
 record['passed']=True
except BaseException as error:record['error']=repr(error);raise
finally:
 if uid:
  pods=json.loads(subprocess.check_output(k+['get','pods','-o','json'],env=env))
  for p in pods['items']:
   n=p['metadata']['name']
   if n.startswith(name+'-'):
    (out/(n+'.json')).write_text(json.dumps(p,indent=2));v=subprocess.run(k+['logs',n,'--all-containers'],env=env,capture_output=True,text=True,timeout=60);(out/(n+'.log')).write_text(v.stdout+v.stderr)
  body=out/'delete.json';body.write_text(json.dumps({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':uid}}));v=subprocess.run(k+['delete','--raw','/apis/nvidia.com/v1beta1/namespaces/heyi-upgrade162/dynamographdeployments/'+name,'-f',str(body)],env=env,capture_output=True,text=True);record['cleanup']={'code':v.returncode,'stdout':v.stdout,'stderr':v.stderr}
 record['finished']=time.time();(out/'result.json').write_text(json.dumps(record,indent=2))
