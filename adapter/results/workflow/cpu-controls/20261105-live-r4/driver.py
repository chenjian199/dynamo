import copy,json,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,'/home/metax/CJ_workspace/dynamo')
from adapter.workflow import core
from adapter.workflow.executors import resources
from adapter.workflow.preflight import ssh_command
from adapter.workflow.executors.transfers import collect

root=core.ROOT/'adapter/results/workflow/cpu-controls/20261105-live-r4'
root.mkdir(parents=True,exist_ok=True)
config='/var/tmp/dynamo-m10/ssh-config-resume'
ns='adapter-workflow-validation'
results=[]
for platform,host,context,workspace,image in [
 ('cuda','h800-6','kubernetes-admin@h800-production','/home/bedicloud/CJ_workspace/adapter-workflow-validation','docker.io/heyi/dynamo-tools-cuda:1.5.1-heyi-m10-4b1efe773c'),
 ('ascend','huawei-52','default','/root/CJ_workspace/adapter-workflow-validation','')]:
 if not image:
  result=subprocess.check_output(ssh_command(host,['crictl','images','-o','json'],config),text=True,timeout=45)
  data=json.loads(result)
  images=[tag for i in data['images'] for tag in i.get('repoTags',[]) if 'dynamo-tools-ascend' in tag]
  if not images: raise RuntimeError('No preloaded Ascend CPU-capable tools image')
  image=images[0]
 path=root/platform;path.mkdir(exist_ok=True)
 env={'nodes':[{'id':'controller','ssh':host,'workspace':workspace}], 'ssh_config':config,'budgets':{'task_timeout_seconds':90,'max_attempts':2,'max_tasks':10},'kubernetes':{'host':host,'context':context,'namespace':ns}}
 frozen={'schema_version':1,'platform':platform,'chip':'workflow-cpu-control','environment':env,'features':{'WF-CONTROL':{'cases':[]}},'planned':['WF-CONTROL'],'required':['WF-CONTROL'],'fixture_kind':'executor recovery only; no chip inference certification'}
 frozen['identity']=core.fingerprint(frozen)
 attempt=path/'features/WF-CONTROL/attempt-001';attempt.mkdir(parents=True,exist_ok=True)
 declared=[{'kind':'Job','name':'workflow-owned-control'},{'kind':'process','name':'sleeper','node':'controller'}]
 state={'identity':frozen['identity'],'features':{'WF-CONTROL':{'task':'running','attempts':[{'path':str(attempt.relative_to(path)),'resources':declared}]}}}
 core.atomic(path/'lock.json',frozen);core.atomic(path/'state.json',state)
 kubectl=lambda args,payload=None:resources.kubectl(env,args,payload)
 exists=kubectl(['get','namespace',ns,'--ignore-not-found','-o','json'])
 if exists:
  if exists['metadata'].get('labels',{}).get('adapter.heyi.io/control-test')!='true':raise RuntimeError('Foreign namespace')
 else:
  kubectl(['create','-f','-','-o','json'],json.dumps({'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns,'labels':{'adapter.heyi.io/control-test':'true'}}}))
 manifest={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':'workflow-owned-control'},'spec':{'backoffLimit':0,'activeDeadlineSeconds':180,'template':{'spec':{'restartPolicy':'Never','terminationGracePeriodSeconds':1,'containers':[{'name':'probe','image':image,'imagePullPolicy':'Never','command':['python3','-c','import time; print("owned",flush=True); time.sleep(120)'],'resources':{'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'100m','memory':'128Mi'}}}]}}}}
 core.atomic(path/'job.json',manifest)
 try:
  first=resources.launch_job(path,'WF-CONTROL','attempt-001',path/'job.json')
  second=resources.launch_job(path,'WF-CONTROL','attempt-001',path/'job.json')
  assert first['uid']==second['uid']
  proc=resources.launch_process(path,'WF-CONTROL','attempt-001','controller','sleeper',['sleep','120'])
  proc2=resources.launch_process(path,'WF-CONTROL','attempt-001','controller','sleeper',['sleep','120'])
  assert proc['pid']==proc2['pid']
  observed=resources.observe(path,'WF-CONTROL','attempt-001')
  assert not observed['owned_resources_absent']
  core.atomic(path/'before.json',observed)
  # Simulate coordinator losing its acknowledgement; resources survive disconnected SSH.
  saved=core.read(attempt/'resources.live.json');(attempt/'resources.live.json').unlink()
  adopted=resources.observe(path,'WF-CONTROL','attempt-001')
  assert not adopted['owned_resources_absent']
  assert core.read(attempt/'resources.live.json')==saved
  core.atomic(path/'adopted.json',adopted)
  stopped=resources.observe(path,'WF-CONTROL','attempt-001',stop=True)
  core.atomic(path/'after.json',stopped)
  assert stopped['owned_resources_absent'],stopped
  resources.recover(path,'WF-CONTROL')
  # Remote bytes collected into the same local results attempt hierarchy.
  receipt=collect(path,'controller','WF-CONTROL','attempt-001')
  core.atomic(path/'collection-receipt.json',receipt)
  results.append({'platform':platform,'job_uid':first['uid'],'process_pid':proc['pid'],'adopted_without_duplicate':True,'owned_absent':True,'collected_files':len(receipt['files'])})
 finally:
  cleanup=resources.observe(path,'WF-CONTROL','attempt-001',stop=True)
  core.atomic(path/'cleanup.json',cleanup)
  # Only own labelled namespace; preserve the namespace if any resources remain.
  if cleanup['owned_resources_absent']:
   kubectl(['delete','--raw','/api/v1/namespaces/'+ns,'-f','-'],json.dumps({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':kubectl(['get','namespace',ns,'-o','json'])['metadata']['uid']}}))
core.atomic(root/'summary.json',{'test_kind':'real SSH/Kubernetes CPU control-path checks, not inference or AI-account validation','results':results})
print(json.dumps(results,indent=2))
