# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run all six cross-node PD handoffs plus streaming, cancellation and recovery."""
import json,os,subprocess,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'cluster-services';out=w/'pd-cross-final-r1';out.mkdir();env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'};k=['kubectl','-n','heyi-upgrade162'];name='upgrade-pd-cross-final';record={'started':time.time(),'passed':False,'source_commit':'1aabe435ad56922cf7e12f92f64df2dc39531ccb'}
try:
 for label,argv,timeout in [('apply',k+['apply','-f',str(r/'pd-cross-final-dgd.json')],60),('regression',['python3',str(w/'regress.py'),'--namespace','heyi-upgrade162','--name',name,'--mode','pd','--client',str(w/'client.py'),'--output',str(out/'regression')],3000),('pairs',['python3',str(w/'pairs.py'),'--namespace','heyi-upgrade162','--name',name,'--client',str(w/'topology.py'),'--output',str(out/'pairs')],900)]:
  with (out/(label+'.log')).open('x') as log:p=subprocess.run(argv,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
  record[label]=p.returncode;p.check_returncode()
 record['passed']=True
except BaseException as e:record['error']=repr(e);raise
finally:
 pods=json.loads(subprocess.check_output(k+['get','pods','-o','json'],env=env))
 for pod in pods['items']:
  n=pod['metadata']['name']
  if n.startswith(name+'-'):
   (out/(n+'.json')).write_text(json.dumps(pod,indent=2));v=subprocess.run(k+['logs',n,'--all-containers'],env=env,capture_output=True,text=True,timeout=60);(out/(n+'.log')).write_text(v.stdout+v.stderr)
 v=subprocess.run(k+['get','dgd',name,'-o','json'],env=env,capture_output=True,text=True)
 if v.returncode==0:
  d=json.loads(v.stdout);assert d['metadata']['labels']['heyi.io/campaign']=='upgrade-1.6.2-ascend-20261111';(out/'final-dgd.json').write_text(v.stdout)
  body=out/'delete.json';body.write_text(json.dumps({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':d['metadata']['uid']}}));p=subprocess.run(k+['delete','--raw','/apis/nvidia.com/v1beta1/namespaces/heyi-upgrade162/dynamographdeployments/'+name,'-f',str(body)],env=env,capture_output=True,text=True);record['cleanup']={'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 record['finished']=time.time();(out/'result.json').write_text(json.dumps(record,indent=2))
