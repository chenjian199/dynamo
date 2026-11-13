#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Wait for immutable images, then execute M01 with the unchanged standard workflow."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

root=Path(__file__).resolve().parent
repo=Path('/home/metax/CJ_workspace/dynamo/.worktrees/source-1.6.2-ascend-20261111/.worktrees/dynamo-1.6.2-ascend-20261111')
remote='/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/images-64815280cf-r2/inventory.json'
ssh=['ssh','-F','/var/tmp/dynamo-regression-1028/ssh-config-upgrade-fresh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','huawei-52']
name='upgrade162-m01-9ee6-20261112';campaign=repo/'adapter/results/ascend/910b3'/name
assert not campaign.exists(),campaign
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()=='9ee6c9fa4f19dfc987511dd77c3da2e3b5933c05'
record={'started':time.time(),'status':'waiting_for_digest_inventory','campaign':str(campaign)}
(root/'m01-campaign-launch.json').write_text(json.dumps(record,indent=2)+'\n')
try:
 deadline=time.monotonic()+7200
 while True:
  r=subprocess.run(ssh+['cat '+remote],capture_output=True,text=True,timeout=60)
  if r.returncode==0:
   inventory=json.loads(r.stdout)
   assert len(inventory)==4
   (root/'images-64815280cf-inventory.json').write_text(r.stdout)
   break
  if time.monotonic()>deadline:raise TimeoutError('Image distribution inventory not ready')
  time.sleep(20)
 inputs=root/'campaign-inputs-9ee6'
 subprocess.run(['python3',str(root/'prepare-campaign-inputs.py'),'--repo',str(repo),'--inventory',str(root/'images-64815280cf-inventory.json'),'--output',str(inputs)],check=True)
 scope=json.loads((inputs/'scope.json').read_text())
 scope['features']={k:v for k,v in scope['features'].items() if k.startswith('M01-') or v['disposition']=='deferred'}
 scope['dependency_choices']={}
 (inputs/'m01-scope.json').write_text(json.dumps(scope,indent=2,ensure_ascii=False)+'\n')
 recipes={}
 controller=root/'m01-controller.py'
 for fid in ('M01-F01','M01-F02','M01-F03'):
  cid=fid+'-C01'
  recipe={'prepare':[],'resources':[]}
  for phase in ('run','observe','cleanup'):
   recipe[phase]=[{'argv':['python3',str(controller),phase,'--attempt','{attempt}','--case',cid,
                          '--entrypoint','{repo}/adapter/modules/m01-platform-contract/common/discovery/platform-smoke.py']}]
  recipes[cid]=recipe
 (inputs/'m01-recipes.json').write_text(json.dumps(recipes,indent=2)+'\n')
 commands=[['init','--campaign',name,'--platform','ascend','--chip','910b3','--environment',str(inputs/'environment.json'),'--scope',str(inputs/'m01-scope.json')],
           ['doctor','--campaign',str(campaign)],
           ['compile','--campaign',str(campaign),'--recipes',str(inputs/'m01-recipes.json')],
           ['seal-plan','--campaign',str(campaign)],
           ['run','--campaign',str(campaign)]]
 record['status']='running';record['commands']=commands
 (root/'m01-campaign-launch.json').write_text(json.dumps(record,indent=2)+'\n')
 for i,cmd in enumerate(commands):
  with (root/f'm01-campaign-{i}-{cmd[0]}.log').open('x') as log:
   subprocess.run(['python3','-m','adapter.workflow',*cmd],cwd=repo,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=7200)
  if cmd[0]=='doctor':assert json.loads((campaign/'preflight.json').read_text())['passed']
  if cmd[0]=='compile':assert not json.loads((campaign/'planning-gaps.json').read_text())['missing']
 state=json.loads((campaign/'state.json').read_text())
 record['features']={k:{x:v.get(x) for x in ('test','qualification')} for k,v in state['features'].items()}
 record['status']='verified' if all(v['qualification']=='verified' for v in record['features'].values()) else 'not_qualified'
except Exception as e:
 record['status']='failed';record['error']=repr(e)
finally:
 record['finished']=time.time()
 (root/'m01-campaign-launch.json').write_text(json.dumps(record,indent=2)+'\n')
 print(json.dumps(record,indent=2),flush=True)
