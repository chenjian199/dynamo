#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run G2/G3 and disk-aware routing with paired native control after serialized prerequisites."""
import datetime,json,subprocess,time
from pathlib import Path
r=Path(__file__).resolve().parent
repo=Path('/home/metax/CJ_workspace/dynamo/.worktrees/source-1.6.2-ascend-20261111/.worktrees/dynamo-1.6.2-ascend-20261111')
base=repo/'adapter/results/ascend/910b3';source=base/'upgrade162-foundation-2a239c2b50-20261112'
name='upgrade162-cache-2a239c2b50-20261112';campaign=base/name;inputs=r/'campaign-inputs-2a239c2b50'
status=r/'cache-launch-2a239c2b50.json';assert not status.exists() and not campaign.exists()
record={'started':time.time(),'status':'waiting_for_prerequisites','campaign':str(campaign)};sequence=0
save=lambda:status.write_text(json.dumps(record,indent=2)+'\n');save()
def wf(args):
 global sequence
 with (r/f'cache-{sequence:02d}-{args[0]}.log').open('x') as f:subprocess.run(['python3','-m','adapter.workflow',*args],cwd=repo,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=10800)
 sequence+=1
try:
 deadline=time.monotonic()+21600
 while True:
  p=r/'transfer-launch-2a239c2b50.json';data=json.loads(p.read_text()) if p.exists() else {}
  if data.get('finished'):
   assert data['status']=='verified',data
   break
  if time.monotonic()>deadline:raise TimeoutError('Foundation incomplete')
  time.sleep(30)
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()=='2a239c2b500ea7e48c86b7216b9878158db4802a'
 full=json.loads((inputs/'scope.json').read_text());scope=json.loads((inputs/'foundation-scope.json').read_text());recipes=json.loads((inputs/'foundation-recipes.json').read_text())
 keys=['M06-F01']
 for fid in keys:scope['features'][fid]=full['features'][fid]
 for fid in keys:
  for i in range(1,4):
   cid=fid+f'-C{i:02d}';controller='cache-case-controller.py'
   entry='adapter/modules/m06-kvbm-collectives/common/cache/'+('routing.py' if i==3 else 'run.py')
   assert (repo/entry).is_file(),entry
   recipes[cid]={'prepare':[],'resources':[],**{phase:[{'argv':['python3',str(r/controller),phase,'--attempt','{attempt}','--case',cid,'--entrypoint','{repo}/'+entry]}] for phase in ('run','observe','cleanup')}}
 for filename,value in [('cache-scope.json',scope),('cache-recipes.json',recipes)]:
  with (inputs/filename).open('x') as f:f.write(json.dumps(value,indent=2)+'\n')
 wf(['init','--campaign',name,'--platform','ascend','--chip','910b3','--environment',str(inputs/'environment.json'),'--scope',str(inputs/'cache-scope.json')])
 wf(['doctor','--campaign',str(campaign)]);assert json.loads((campaign/'preflight.json').read_text())['passed']
 previous=json.loads((source/'state.json').read_text())
 for fid,item in previous['features'].items():
  if fid not in scope['features'] or scope['features'][fid]['disposition']=='deferred':continue
  assert item['qualification']=='verified'
  wf(['inherit','--campaign',str(campaign),'--feature',fid,'--source',str(source),'--reviewed-by','Codex','--rationale','Exact source, immutable image, model, topology and environment inputs; original hashed execution is retained and independently rechecked.'])
 wf(['compile','--campaign',str(campaign),'--recipes',str(inputs/'cache-recipes.json')]);assert not json.loads((campaign/'planning-gaps.json').read_text())['missing']
 wf(['seal-plan','--campaign',str(campaign)]);record['status']='running';save()
 for fid in keys:
  age=datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(json.loads((campaign/'preflight.json').read_text())['time'])
  if age.total_seconds()>3000:wf(['doctor','--campaign',str(campaign)])
  record['active_feature']=fid;save();wf(['run','--campaign',str(campaign),'--feature',fid])
  state=json.loads((campaign/'state.json').read_text());record['features']={k:{x:v.get(x) for x in ('task','test','qualification')} for k,v in state['features'].items()};save()
  assert state['features'][fid]['qualification']=='verified',state['features'][fid]
 record['status']='verified'
except Exception as e:record.update(status='failed',error=repr(e))
finally:
 record['finished']=time.time();save();print(json.dumps(record,indent=2),flush=True)
