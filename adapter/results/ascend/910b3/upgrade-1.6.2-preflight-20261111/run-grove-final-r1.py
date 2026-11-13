# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run real gang barrier and inference/scaling/recovery on the dedicated 7.7 node using the final1aabe image."""
import json,os,subprocess,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'cluster-services';out=w/'grove-final-r1';out.mkdir();env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'};k=['kubectl','-n','heyi-upgrade162'];name='upgrade-grove-final-r1';record={'started':time.time(),'passed':False,'source_commit':'1aabe435ad56922cf7e12f92f64df2dc39531ccb'}
try:
 deadline=time.monotonic()+2400
 while True:
  data=json.loads(subprocess.check_output(k+['get','pods','-A','-o','json'],env=env))
  busy=[p['metadata']['name'] for p in data['items'] if p['spec'].get('nodeName')=='master-02-45d4a763' and p.get('status',{}).get('phase') not in ['Succeeded','Failed'] and any(int(c.get('resources',{}).get('limits',{}).get('huawei.com/Ascend910',0)) for c in p['spec']['containers'])]
  if (r/'profiling-r5/online-result.json').exists() and not busy:break
  if time.monotonic()>deadline:raise TimeoutError('Waiting for final online cleanup on52: '+repr(busy))
  time.sleep(10)
 for label,argv,timeout in [('barrier',['python3',str(w/'gang.py'),'--manifest',str(r/'grove-final-r1.json'),'--output',str(out/'barrier')],420),('regression',['python3',str(w/'regress.py'),'--namespace','heyi-upgrade162','--name',name,'--client',str(w/'client.py'),'--output',str(out/'regression')],3600)]:
  with (out/(label+'.log')).open('x') as log:p=subprocess.run(argv,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
  record[label]=p.returncode;p.check_returncode()
 record['passed']=True
except BaseException as e:record['error']=repr(e);raise
finally:
 v=subprocess.run(k+['get','dgd',name,'-o','json'],env=env,capture_output=True,text=True)
 if v.returncode==0:
  d=json.loads(v.stdout);assert d['metadata']['labels']['heyi.io/campaign']=='upgrade-1.6.2-ascend-20261111';(out/'final-dgd.json').write_text(v.stdout)
  body=out/'delete.json';body.write_text(json.dumps({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':d['metadata']['uid']}}));p=subprocess.run(k+['delete','--raw','/apis/nvidia.com/v1beta1/namespaces/heyi-upgrade162/dynamographdeployments/'+name,'-f',str(body)],env=env,capture_output=True,text=True);record['cleanup']={'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 record['finished']=time.time();(out/'result.json').write_text(json.dumps(record,indent=2))
