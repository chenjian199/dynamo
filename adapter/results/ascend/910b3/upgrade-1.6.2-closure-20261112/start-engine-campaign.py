#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run M03 baseline, lifecycle and KV routing after verified M01/M02 dependencies."""
import json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parent
repo=Path('/home/metax/CJ_workspace/dynamo/.worktrees/source-1.6.2-ascend-20261111/.worktrees/dynamo-1.6.2-ascend-20261111')
base=repo/'adapter/results/ascend/910b3';name='upgrade162-engine-9ee6-20261112';campaign=base/name
assert not campaign.exists()
record={'started':time.time(),'status':'waiting_for_dependencies','campaign':str(campaign)}
status=root/'engine-campaign-launch.json';status.write_text(json.dumps(record,indent=2)+'\n')
try:
 deadline=time.monotonic()+10800
 for filename in ['m01-campaign-launch.json','m02dev-campaign-launch.json']:
  while True:
   data=json.loads((root/filename).read_text())
   if data.get('finished'):
    assert data['status']=='verified',data
    break
   if time.monotonic()>deadline:raise TimeoutError('Prerequisite campaign unfinished')
   time.sleep(15)
 inputs=root/'campaign-inputs-9ee6';full=json.loads((inputs/'scope.json').read_text())
 keys=['M01-F01','M02-F01','M03-F01','M03-F02','M03-F03']
 scope={'features':{k:v for k,v in full['features'].items() if k in keys or v['disposition']=='deferred'},'dependency_choices':{}}
 (inputs/'engine-scope.json').write_text(json.dumps(scope,indent=2,ensure_ascii=False)+'\n')
 recipes={}
 for fid,cases in [('M03-F01',[1]),('M03-F02',[1,2,3]),('M03-F03',[1])]:
  for index in cases:
   cid=fid+f'-C{index:02d}';entry='rank-smoke.py' if fid=='M03-F03' else 'run-case.py'
   recipes[cid]={'prepare':[],'resources':[],**{phase:[{'argv':['python3',str(root/'engine-case-controller.py'),phase,'--attempt','{attempt}','--case',cid,'--entrypoint','{repo}/adapter/modules/m03-engine-integration/common/engine/'+entry]}] for phase in ['run','observe','cleanup']}}
 (inputs/'engine-recipes.json').write_text(json.dumps(recipes,indent=2)+'\n')
 commands=[['init','--campaign',name,'--platform','ascend','--chip','910b3','--environment',str(inputs/'environment.json'),'--scope',str(inputs/'engine-scope.json')],['doctor','--campaign',str(campaign)]]
 for key,source in [('M01-F01','upgrade162-m01-9ee6-20261112'),('M02-F01','upgrade162-m02dev-9ee6-20261112')]:
  commands.append(['inherit','--campaign',str(campaign),'--feature',key,'--source',str(base/source),'--reviewed-by','Codex','--rationale','Exact frozen source, stack, environment and feature input identity; preserve original current-run evidence without claiming a new hardware run.'])
 commands += [['compile','--campaign',str(campaign),'--recipes',str(inputs/'engine-recipes.json')],['seal-plan','--campaign',str(campaign)],['run','--campaign',str(campaign)]]
 record['status']='running';record['commands']=commands;status.write_text(json.dumps(record,indent=2)+'\n')
 for i,cmd in enumerate(commands):
  with (root/f'engine-campaign-{i}-{cmd[0]}.log').open('x') as f:subprocess.run(['python3','-m','adapter.workflow',*cmd],cwd=repo,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=21600)
  if cmd[0]=='doctor':assert json.loads((campaign/'preflight.json').read_text())['passed']
  if cmd[0]=='compile':assert not json.loads((campaign/'planning-gaps.json').read_text())['missing']
 state=json.loads((campaign/'state.json').read_text())
 record['features']={k:{x:v.get(x) for x in ('test','qualification')} for k,v in state['features'].items()}
 record['status']='verified' if all(v['qualification']=='verified' for v in record['features'].values()) else 'not_qualified'
except Exception as e:record['status']='failed';record['error']=repr(e)
finally:
 record['finished']=time.time();status.write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2),flush=True)
