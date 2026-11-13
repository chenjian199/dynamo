# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Replay the native CLI diagnostic workload against a smoke-ready endpoint."""
import argparse,concurrent.futures,json,time,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(exist_ok=False);rows=[];report={'started':time.time(),'passed':False,'url':a.url}
def request(index):
 payload={'model':'upgrade-deepseek','prompt':'The capital of France is','temperature':0,'seed':42,'max_tokens':32}
 req=urllib.request.Request(a.url+'/v1/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'});start=time.time()
 with urllib.request.urlopen(req,timeout=180) as f:response=json.load(f)
 return {'index':index,'request':payload,'response':response,'latency_s':time.time()-start}
try:
 for batch in [1,1,1,1,1,1,4,4,4,4]:
  start=time.time()
  with concurrent.futures.ThreadPoolExecutor(max_workers=batch) as pool:result=list(pool.map(request,range(batch)))
  rows.append({'concurrency':batch,'wall_s':time.time()-start,'requests':result});(a.output/'responses.json').write_text(json.dumps(rows,indent=2))
 unique={r['response']['choices'][0]['text'] for batch in rows for r in batch['requests']}
 assert all(r['response']['usage']['completion_tokens']>0 for batch in rows for r in batch['requests'])
 report.update(passed=True,requests=sum(len(b['requests']) for b in rows),unique_outputs=len(unique),deterministic=len(unique)==1,scope='API liveness and repeatability control, not an SLO or performance certification')
except BaseException as error:report['error']=repr(error);raise
finally:report['finished']=time.time();(a.output/'result.json').write_text(json.dumps(report,indent=2))
