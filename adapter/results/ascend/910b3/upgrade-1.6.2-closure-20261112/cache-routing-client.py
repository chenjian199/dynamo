#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the unchanged routing entrypoint while retaining real completion token IDs."""
import hashlib,json,runpy,sys,time
from pathlib import Path
import requests
entry=Path(sys.argv[1]);real_post=requests.post
out=Path('/evidence/routing-http.jsonl')
def post(url,*args,**kwargs):
 payload=kwargs.get('json')
 if payload and url.endswith('/v1/completions'):
  nvext=dict(payload.get('nvext',{}));nvext['extra_fields']=sorted(set(nvext.get('extra_fields',[]))|{'completion_token_ids'})
  kwargs={**kwargs,'json':{**payload,'nvext':nvext}}
 response=real_post(url,*args,**kwargs)
 with out.open('a') as f:f.write(json.dumps({'url':url,'request':kwargs.get('json'),'status':response.status_code,'response':response.json(),'time_ns':time.time_ns()})+'\n')
 return response
requests.post=post
Path('/evidence/entry-invocations.json').write_text(json.dumps([{'argv':sys.argv[1:],'entrypoint_sha256':hashlib.sha256(entry.read_bytes()).hexdigest()}],indent=2)+'\n')
sys.argv=sys.argv[1:];runpy.run_path(str(entry),run_name='__main__')
