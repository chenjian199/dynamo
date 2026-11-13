# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sequential three-arm comparison with UID-scoped cleanup and raw capture."""
import json,os,subprocess,time
from pathlib import Path
r=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/comparison-r2');k=['kubectl','-n','heyi-upgrade162'];env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'};record={'started':time.time(),'arms':{},'passed':False};client='closure-comparison-tools'
def command(argv,timeout=60):return subprocess.run(argv,env=env,capture_output=True,text=True,timeout=timeout)
def get(kind,name):
 p=command(k+['get',kind,name,'-o','json']);p.check_returncode();return json.loads(p.stdout)
def remove(kind,name,uid,out):
 plural={'pod':'pods','service':'services','dgd':'dynamographdeployments'}[kind];prefix='/api/v1' if kind!='dgd' else '/apis/nvidia.com/v1beta1';body=out/(name+'-delete.json');body.write_text(json.dumps({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':uid}}));p=command(k+['delete','--raw',prefix+'/namespaces/heyi-upgrade162/'+plural+'/'+name,'-f',str(body)]);(out/(name+'-delete.log')).write_text(p.stdout+p.stderr);p.check_returncode()
def ready(kind,name,limit=900):
 end=time.monotonic()+limit
 while time.monotonic()<end:
  x=get(kind,name)
  if kind=='pod':ok=any(c['type']=='Ready' and c['status']=='True' for c in x.get('status',{}).get('conditions',[]))
  else:ok=x.get('status',{}).get('state')=='successful'
  if ok:return x
  time.sleep(5)
 raise TimeoutError(name+' readiness')
try:
 p=command(k+['apply','-f',str(r/'tools.json')]);p.check_returncode();c=ready('pod',client,300);record['client_uid']=c['metadata']['uid'];(r/'client-pod.json').write_text(json.dumps(c,indent=2))
 for role in ['native','old_heyi']:
  out=r/role;out.mkdir();state={'started':time.time(),'passed':False};record['arms'][role]=state;kind='pod' if role=='native' else 'dgd';name='closure-compare-'+role.replace('_','-');owned=[]
  try:
   manifest=json.loads((r/(role+'.json')).read_text());image=manifest['spec']['containers'][0]['image'] if role=='native' else manifest['spec']['components'][0]['podTemplate']['spec']['containers'][0]['image']
   deadline=time.monotonic()+1800
   while time.monotonic()<deadline:
    node=json.loads(command(['kubectl','get','node','master','-o','json']).stdout)
    if any(image in i['names'] or ('docker.io/'+image) in i['names'] for i in node['status']['images']):break
    time.sleep(10)
   else:raise TimeoutError('Image not advertised on 7.5: '+image)
   (out/'node.json').write_text(json.dumps(node,indent=2))
   if role=='native':
    p=command(k+['apply','-f',str(r/'native-service.json')]);p.check_returncode();owned.append(('service',name,get('service',name)['metadata']['uid']))
   p=command(k+['apply','-f',str(r/(role+'.json'))]);(out/'apply.log').write_text(p.stdout+p.stderr);p.check_returncode();owned.append((kind,name,get(kind,name)['metadata']['uid']))
   x=ready(kind,name);(out/'ready.json').write_text(json.dumps(x,indent=2));state['uid']=x['metadata']['uid']
   active=[get('pod',name)] if role=='native' else json.loads(command(k+['get','pods','-l','nvidia.com/dynamo-graph-deployment-name='+name,'-o','json']).stdout)['items']
   for pod in active:
    podname=pod['metadata']['name']
    probe='import json,importlib.metadata as m; from pathlib import Path; p=Path("/opt/dynamo/artifacts/SOURCE.json"); print(json.dumps({"vllm":m.version("vllm"),"torch":m.version("torch"),"source":json.loads(p.read_text()) if p.exists() else None}))'
    argv=k+['exec',podname,'--',('/opt/dynamo/venv/bin/python' if role!='native' else 'python3'),'-c',probe]
    p=command(argv,60);(out/(podname+'.installed.json')).write_text(p.stdout);(out/(podname+'.installed.stderr')).write_text(p.stderr);p.check_returncode()
   (out/'runtime-pods.json').write_text(json.dumps(active,indent=2))

   url='http://'+name+('' if role=='native' else '-frontend')+'.heyi-upgrade162.svc:8000'
   argv=k+['exec',client,'--','/opt/dynamo/tools-venv/bin/python','/comparison/measure.py','--url',url,'--workload','/comparison/workload.json','--load-script','/comparison/load.py','--output','/comparison/'+role+'/measure.json']
   (out/'argv.json').write_text(json.dumps(argv,indent=2));p=command(argv,900);(out/'measure.log').write_text(p.stdout+p.stderr);state['exit_code']=p.returncode;state['passed']=p.returncode==0 and json.loads((out/'measure.json').read_text())['passed']
  except Exception as e:state['error']=repr(e)
  finally:
   try:
    pods=[get('pod',name)] if role=='native' and owned else json.loads(command(k+['get','pods','-l','nvidia.com/dynamo-graph-deployment-name='+name,'-o','json']).stdout)['items']
    (out/'pods.json').write_text(json.dumps(pods,indent=2))
    for pod in pods:
     p=command(k+['logs',pod['metadata']['name']],120);(out/(pod['metadata']['name']+'.log')).write_text(p.stdout+p.stderr)
   except Exception as e:state['capture_error']=repr(e)
   for ownkind,ownname,uid in reversed(owned):remove(ownkind,ownname,uid,out)
   deadline=time.monotonic()+300
   while time.monotonic()<deadline:
    pods=json.loads(command(k+['get','pods','-o','json']).stdout)['items'];remaining=[p for p in pods if p['metadata']['name']==name or p['metadata'].get('labels',{}).get('nvidia.com/dynamo-graph-deployment-name')==name]
    if not remaining:break
    time.sleep(3)
   else:raise TimeoutError('Owned workload was not fully removed: '+name)
   state['cleanup_complete']=True;state['finished']=time.time();(out/'result.json').write_text(json.dumps(state,indent=2));(r/'result.json').write_text(json.dumps(record,indent=2))
 record['completed_arms_passed']=all(x['passed'] for x in record['arms'].values());record['passed']=False;record['remaining_arms']=['new_heyi']
finally:
 if record.get('client_uid'):remove('pod',client,record['client_uid'],r)
 record['finished']=time.time();(r/'result.json').write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2),flush=True)
