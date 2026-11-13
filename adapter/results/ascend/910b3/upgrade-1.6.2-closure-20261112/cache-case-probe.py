#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute the canonical cache harness and retain paired generated token IDs."""
import hashlib,importlib.util,json,sys,time
from pathlib import Path
import requests
spec=json.loads(Path(sys.argv[1]).read_text());entry=Path('/harness')/spec['entrypoint']
assert hashlib.sha256(entry.read_bytes()).hexdigest()==spec['entrypoint_sha256']
assert spec['case'] in ('M06-F01-C01','M06-F01-C02')
sys.path.insert(0,'/harness')
module_spec=importlib.util.spec_from_file_location('closure_cache_harness',entry)
module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
real_post=requests.post;capture=None

def observed_post(url,*args,**kwargs):
 payload=kwargs.get('json')
 if payload and url.endswith('/v1/completions'):
  kwargs={**kwargs,'json':{**payload,'return_token_ids':True}}
 started=time.time_ns();response=real_post(url,*args,**kwargs)
 row={'started_ns':started,'finished_ns':time.time_ns(),'url':url,'request':kwargs.get('json'),'status':response.status_code,'stream':bool(kwargs.get('stream'))}
 if not row['stream']:
  try:row['response']=response.json()
  except ValueError:row['body']=response.text
 with capture.open('a') as f:f.write(json.dumps(row)+'\n')
 return response
requests.post=observed_post
commands=[]
for arm in ('baseline','cache'):
 capture=Path('/evidence')/(arm+'-http.jsonl')
 argv=[str(entry),'--platform','ascend','--model',spec['container_model'],'--devices',','.join(map(str,spec['devices'])),
       '--tp',str(spec['topology']['tp']),'--cycles','1','--output','/evidence/'+arm,'--disable-custom-all-reduce']
 if arm=='baseline':argv+=['--baseline']
 else:
  argv+=['--baseline-summary','/evidence/baseline/summary.json']
  if spec['case']=='M06-F01-C02':argv+=['--disk']
 commands.append({'argv':argv,'entrypoint_sha256':hashlib.sha256(entry.read_bytes()).hexdigest()})
 Path('/evidence/entry-invocations.json').write_text(json.dumps(commands,indent=2)+'\n')
 sys.argv=argv;module.main()
