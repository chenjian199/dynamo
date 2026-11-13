# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fixed-count upgrade comparison using the shared streaming metric implementation."""
import argparse,asyncio,hashlib,importlib.util,json,time
from pathlib import Path
from types import SimpleNamespace
import aiohttp

async def run(a):
 w=json.loads(a.workload.read_text());spec=importlib.util.spec_from_file_location('comparison_load',a.load_script);load=importlib.util.module_from_spec(spec);spec.loader.exec_module(load)
 report={'passed':False,'workload_sha256':hashlib.sha256(a.workload.read_bytes()).hexdigest(),'load_script_sha256':hashlib.sha256(a.load_script.read_bytes()).hexdigest(),'started':time.time(),'warmup':[],'rounds':[],'correctness':[]}
 opts=SimpleNamespace(url=a.url,model=w['model'],isl=w['input_tokens'],osl=w['output_tokens'],token_id_metrics=False)
 try:
  async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=w['request_timeout_seconds'])) as session:
   for case in w['correctness_prompts']:
    payload={'model':w['model'],'messages':[{'role':'user','content':case['prompt']}],'temperature':0,'max_tokens':32,'chat_template_kwargs':{'enable_thinking':False}}
    async with session.post(a.url+'/v1/chat/completions',json=payload) as response:
     text=await response.text();record={'payload':payload,'status':response.status,'body':text};report['correctness'].append(record)
     assert response.status==200,record
     body=json.loads(text);content=body['choices'][0]['message']['content'];assert case['required_text'] in content.lower(),record
   for i in range(w['warmup_requests']):report['warmup'].append(await load.request(session,opts,i))
   for round_index in range(w['rounds']):
    semaphore=asyncio.Semaphore(w['concurrency']);records=[];errors=[]
    async def one(i):
     async with semaphore:
      try:
       result=await load.request(session,opts,i)
       assert result['prompt_tokens']==w['input_tokens'] and result['completion_tokens']==w['output_tokens'],result
       records.append(result)
      except Exception as e:errors.append({'sequence':i,'error':repr(e)})
    started=time.perf_counter();await asyncio.gather(*(one(i) for i in range(w['measured_requests_per_round'])));elapsed=time.perf_counter()-started
    report['rounds'].append({'index':round_index,'elapsed':elapsed,'requests':records,'errors':errors,'throughput':len(records)/elapsed,'ttft_p95_ms':load.percentile([x['ttft_ms'] for x in records]),'tpot_p95_ms':load.percentile([x['tpot_ms'] for x in records])})
    a.output.write_text(json.dumps(report,indent=2)+'\n')
   records=[x for r in report['rounds'] for x in r['requests']];elapsed=sum(r['elapsed'] for r in report['rounds']);report['failed_requests']=sum(len(r['errors']) for r in report['rounds'])
   report['metrics']={'throughput':len(records)/elapsed,'output_tokens_per_second':sum(x['completion_tokens'] for x in records)/elapsed,'ttft_p95_ms':load.percentile([x['ttft_ms'] for x in records]),'tpot_p95_ms':load.percentile([x['tpot_ms'] for x in records])}
   report['passed']=report['failed_requests']==0 and len(records)==w['rounds']*w['measured_requests_per_round']
 except Exception as e:report['error']=repr(e);raise
 finally:
  report['finished']=time.time();a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ['rounds','warmup','correctness']}),flush=True)
 if not report['passed']:raise RuntimeError('Comparison had failed requests')

p=argparse.ArgumentParser();p.add_argument('--workload',type=Path,required=True);p.add_argument('--load-script',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--url',required=True);asyncio.run(run(p.parse_args()))
