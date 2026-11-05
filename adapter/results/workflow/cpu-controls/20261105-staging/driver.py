import json,sys,subprocess
from pathlib import Path
sys.path.insert(0,'/home/metax/CJ_workspace/dynamo')
from adapter.workflow import core
from adapter.workflow.executors.transfers import stage,stage_inputs,collect
from adapter.workflow.preflight import ssh_command
root=core.ROOT/'adapter/results/workflow/cpu-controls/20261105-staging'
root.mkdir(parents=True)
results=[]
for platform,host,workspace in [('cuda','h800-6','/home/bedicloud/CJ_workspace/adapter-workflow-stage-20261105'),('ascend','huawei-52','/root/CJ_workspace/adapter-workflow-stage-20261105')]:
 p=root/platform;p.mkdir()
 frozen={'identity':None,'environment':{'nodes':[{'id':'node','ssh':host,'workspace':workspace}],'ssh_config':'/var/tmp/dynamo-m10/ssh-config-resume','budgets':{'task_timeout_seconds':90}},'source':{'files':{name:core.digest(core.ROOT/name) for name in ['AGENTS.md','adapter/workflow/runtime_probe.py']}},'features':{'WF-STAGE':{'cases':[]}},'fixture_kind':'Transfer control only; partial source inventory, no hardware qualification'}
 frozen.pop('identity');frozen['identity']=core.fingerprint(frozen)
 attempt=p/'features/WF-STAGE/attempt-001';attempt.mkdir(parents=True)
 core.atomic(p/'lock.json',frozen);core.atomic(p/'state.json',{'identity':frozen['identity'],'features':{'WF-STAGE':{'attempts':[{'path':str(attempt.relative_to(p))}]}}})
 for name in ('task.json','envelope.json','preflight.json','resources.json'):core.atomic(attempt/name,{'fixture':'same bytes','identity':frozen['identity']})
 source_receipt=stage(p,'node');input_receipt=stage_inputs(p,'node','WF-STAGE','attempt-001');collected=collect(p,'node','WF-STAGE','attempt-001')
 assert all(core.digest(attempt/name)==collected['files'][name] for name in ('task.json','envelope.json','preflight.json','resources.json'))
 results.append({'platform':platform,'source':source_receipt,'inputs':input_receipt,'collection':collected,'bytes_match':True})
core.atomic(root/'summary.json',{'test_kind':'real SSH stage, input staging and collection','results':results})
print(json.dumps({'platforms':[r['platform'] for r in results],'bytes_match':True}))
