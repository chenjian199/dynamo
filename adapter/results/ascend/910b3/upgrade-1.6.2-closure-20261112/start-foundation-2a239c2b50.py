#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Freeze exact-source image evidence and execute required foundation features serially."""
import datetime,json,subprocess,time
from pathlib import Path
r=Path(__file__).resolve().parent
repo=Path('/home/metax/CJ_workspace/dynamo/.worktrees/source-1.6.2-ascend-20261111/.worktrees/dynamo-1.6.2-ascend-20261111')
commit='2a239c2b500ea7e48c86b7216b9878158db4802a'
name='upgrade162-foundation-2a239c2b50-20261112';campaign=repo/'adapter/results/ascend/910b3'/name
inputs=r/'campaign-inputs-2a239c2b50';status=r/'foundation-launch-2a239c2b50.json'
assert not campaign.exists() and not status.exists()
record={'status':'waiting_for_images_and_prior_cleanup','started':time.time(),'campaign':str(campaign),'source_commit':commit}
def save():status.write_text(json.dumps(record,indent=2)+'\n')
save()
ssh=['ssh','-F','/var/tmp/dynamo-regression-1028/ssh-config-upgrade-fresh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes']
remote='/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/'
sequence=0

def run(cmd,label,timeout=21600):
 global sequence
 with (r/f'foundation-{sequence:02d}-{label}.log').open('x') as f:subprocess.run(cmd,cwd=repo,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=timeout)
 sequence+=1

def workflow(args,label,timeout=21600):run(['python3','-m','adapter.workflow',*args],label,timeout)
try:
 deadline=time.monotonic()+10800
 while True:
  old=r/'engine-resume-r2.json'
  prior=json.loads(old.read_text()) if old.exists() else {}
  receipts={}
  for host,folder in [('huawei-52','images-2a239c2b50'),('huawei-55','images-2a239c2b50-55')]:
   result=subprocess.run(ssh+[host,'cat '+remote+folder+'/status.json'],capture_output=True,text=True,timeout=30)
   if result.returncode==0:
    data=json.loads(result.stdout)
    if data['status']=='failed':raise RuntimeError({host:data})
    receipts[host]=data
  if prior.get('finished') and receipts.get('huawei-52',{}).get('status')=='controller_imported' and receipts.get('huawei-55',{}).get('status')=='imported':
   assert prior['status']=='verified',prior
   break
  if time.monotonic()>deadline:raise TimeoutError('Images or preceding cleanup are incomplete')
  time.sleep(30)
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()==commit
 for host,data in receipts.items():(r/('final-inventory-'+host+'-2a239c2b50.json')).write_text(json.dumps(data,indent=2)+'\n')
 inventory=r/'inventory-2a239c2b50.json';inventory.write_text(json.dumps(receipts['huawei-52']['inventory'],indent=2)+'\n')
 run(['python3',str(r/'prepare-campaign-inputs-r2.py'),'--repo',str(repo),'--inventory',str(inventory),'--source-commit',commit,'--output',str(inputs)],'inputs')
 run(['python3',str(r/'prepare-foundation-recipes.py'),'--inputs',str(inputs)],'recipes')
 workflow(['init','--campaign',name,'--platform','ascend','--chip','910b3','--environment',str(inputs/'environment.json'),'--scope',str(inputs/'foundation-scope.json')],'init')
 workflow(['doctor','--campaign',str(campaign)],'doctor');assert json.loads((campaign/'preflight.json').read_text())['passed']
 workflow(['compile','--campaign',str(campaign),'--recipes',str(inputs/'foundation-recipes.json')],'compile')
 assert not json.loads((campaign/'planning-gaps.json').read_text())['missing']
 workflow(['seal-plan','--campaign',str(campaign)],'seal')
 record['status']='running';save()
 for fid in json.loads((campaign/'lock.json').read_text())['planned']:
  age=datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(json.loads((campaign/'preflight.json').read_text())['time'])
  if age.total_seconds()>3000:workflow(['doctor','--campaign',str(campaign)],'refresh-preflight')
  record['active_feature']=fid;save()
  workflow(['run','--campaign',str(campaign),'--feature',fid],fid,9000)
  state=json.loads((campaign/'state.json').read_text());record['features']={k:{x:v.get(x) for x in ('task','test','qualification')} for k,v in state['features'].items()};save()
  assert state['features'][fid]['qualification']=='verified',{fid:state['features'][fid]}
 record['status']='verified'
except Exception as e:record.update(status='failed',error=repr(e))
finally:
 record['finished']=time.time();save();print(json.dumps(record,indent=2),flush=True)
