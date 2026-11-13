#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect the completed new arm, independently recompute, then present standard gate input."""
import json,subprocess,time
from pathlib import Path
r=Path(__file__).resolve().parent;target=r/'comparison-2a239c2b50-remote';assert not target.exists()
ssh=['ssh','-F','/var/tmp/dynamo-regression-1028/ssh-config-upgrade-fresh','-o','BatchMode=yes','huawei-52']
remote='/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/comparison-2a239c2b50'
status=r/'comparison-2a239c2b50-collection.json';assert not status.exists()
record={'started':time.time(),'status':'waiting_for_arm_cleanup'}
try:
 deadline=time.monotonic()+10800
 while True:
  status.write_text(json.dumps(record,indent=2)+'\n')
  p=subprocess.run(ssh+['cat '+remote+'/new-result.json'],capture_output=True,text=True,timeout=30)
  if p.returncode==0:
   data=json.loads(p.stdout)
   if data.get('finished'):break
  if time.monotonic()>deadline:raise TimeoutError('New arm incomplete')
  time.sleep(30)
 subprocess.run(['scp','-O','-r','-F','/var/tmp/dynamo-regression-1028/ssh-config-upgrade-fresh','huawei-52:'+remote,str(target)],check=True,timeout=600)
 assert data['passed'] and all(v.get('cleanup_complete') for v in data['arms'].values()),data
 with (r/'comparison-2a239c2b50-audit.log').open('x') as f:subprocess.run(['python3',str(r/'audit-comparison-2a239c2b50.py')],stdout=f,stderr=subprocess.STDOUT,check=True)
 record['status']='audited';record['standard_input']='adapter/results/workflow/upstream/dynamo-1.6.2-ascend-scoped-20261112/comparison-inputs/2a239c2b50/input.json'
except Exception as e:record.update(status='failed',error=repr(e))
finally:
 record['finished']=time.time();status.write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2),flush=True)
