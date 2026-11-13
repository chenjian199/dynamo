#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Observe native/Dynamo generated token IDs, streaming and cancellation directly."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from prometheus_client.parser import text_string_to_metric_families
from transformers import AutoTokenizer

p=argparse.ArgumentParser();p.add_argument('--arm',choices=['native','dynamo'],required=True)
p.add_argument('--output',type=Path,required=True);p.add_argument('--spec',type=Path,required=True)
a=p.parse_args();spec=json.loads(a.spec.read_text());a.output.mkdir(parents=True,exist_ok=False)
model=spec['container_model'];top=spec['topology'];tp=top.get('tp',1);dp=top.get('dp',1);pp=top.get('pp',1)
base='http://127.0.0.1:8010';served='m03-model'
extra=['--pipeline-parallel-size',str(pp),*spec.get('engine_args',[])]
if a.arm=='dynamo':cmd=['bash','/harness/adapter/modules/m03-engine-integration/common/engine/serve.sh',*extra]
else:cmd=[sys.executable,'-m','vllm.entrypoints.openai.api_server','--host','127.0.0.1','--port','8010',
 '--model',model,'--served-model-name',served,'--enforce-eager','--max-model-len','4096','--max-num-seqs','4',
 '--tensor-parallel-size',str(tp),'--data-parallel-size',str(dp),'--data-parallel-size-local',str(dp),
 '--enable-prefix-caching','--block-size','128','--kv-cache-memory-bytes','8589934592',*extra]
report={'arm':a.arm,'command':cmd,'started':time.time(),'passed':False,'requests':[],'responses':[]}

def http(path,payload=None,raw=False):
 req=urllib.request.Request(base+path,data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=180) as r:
  return r.read().decode() if raw else json.load(r)

def tokens(response):
 value=response.get('nvext',{}).get('completion_token_ids') if a.arm=='dynamo' else response['choices'][0].get('token_ids')
 assert isinstance(value,list) and value and all(isinstance(x,int) for x in value),response
 return value

def payload(prompt,stream=False):
 value={'model':served,'prompt':prompt,'temperature':0,'seed':42,'max_tokens':32,'ignore_eos':True,'stream':stream}
 if a.arm=='dynamo':value['nvext']={'extra_fields':['completion_token_ids']}
 else:value['return_token_ids']=True
 return value

def metric():
 with urllib.request.urlopen('http://127.0.0.1:8090/metrics',timeout=15) as r:raw=r.read().decode()
 values={}
 for family in text_string_to_metric_families(raw):
  for sample in family.samples:
   if sample.name in ('dynamo_component_inflight_requests','dynamo_component_cancellation_total') and sample.labels.get('dynamo_endpoint')=='generate':
    values[sample.name]=values.get(sample.name,0)+sample.value
 return {'text':raw,'values':values}

with (a.output/'server.log').open('x') as log:
 proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 report['pid']=proc.pid
 try:
  deadline=time.monotonic()+spec.get('startup_timeout',900)
  while True:
   if proc.poll() is not None:raise RuntimeError('Server exited before readiness')
   try:
    if served in [x['id'] for x in http('/v1/models')['data']]:break
   except Exception:pass
   if time.monotonic()>deadline:raise TimeoutError('Server startup')
   time.sleep(2)
  tokenizer=AutoTokenizer.from_pretrained(model,local_files_only=True,trust_remote_code=True)
  prompts=[tokenizer.encode(x,add_special_tokens=False) for x in ['The capital of France is','Calculate 2 + 3. Answer:']]
  report['prompt_token_ids']=prompts
  for prompt in prompts:
   req=payload(prompt);report['requests'].append(req)
   result=http('/v1/completions',req);report['responses'].append(result)
   assert len(tokens(result))==32 and result['choices'][0]['finish_reason']=='length'
  req=payload(prompts[0],True);chunks=[];ids=[];done=False
  request=urllib.request.Request(base+'/v1/completions',data=json.dumps(req).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(request,timeout=180) as response:
   for line in response:
    if not line.startswith(b'data: '):continue
    data=line[6:].strip()
    if data==b'[DONE]':done=True;break
    chunk=json.loads(data);chunks.append(chunk)
    if a.arm=='dynamo':ids.extend(chunk.get('nvext',{}).get('completion_token_ids') or [])
    elif chunk.get('choices'):ids.extend(chunk['choices'][0].get('token_ids') or [])
  report['stream']={'request':req,'chunks':chunks,'done':done,'token_ids':ids}
  assert done and ids==tokens(report['responses'][0]),report['stream']
  with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda prompt:http('/v1/completions',payload(prompt)),prompts))
  report['concurrent']=results
  report['concurrent_matches_serial'] = [tokens(x) for x in results]==[tokens(x) for x in report['responses']]
  if a.arm=='dynamo':
   report['before_cancel']=metric()
   req={**payload(prompts[0],True),'max_tokens':2048}
   request=urllib.request.Request(base+'/v1/completions',data=json.dumps(req).encode(),headers={'Content-Type':'application/json'})
   with urllib.request.urlopen(request,timeout=180) as response:
    for line in response:
     if line.startswith(b'data: ') and line[6:].strip()!=b'[DONE]':
      chunk=json.loads(line[6:]);generated=chunk.get('nvext',{}).get('completion_token_ids')
      if generated:report['cancel_first_chunk']=chunk;break
    else:raise RuntimeError('No token before cancellation')
   deadline=time.monotonic()+60
   while True:
    report['after_cancel']=metric();values=report['after_cancel']['values']
    if values.get('dynamo_component_inflight_requests')==0 and values.get('dynamo_component_cancellation_total',0)>report['before_cancel']['values'].get('dynamo_component_cancellation_total',0):break
    if time.monotonic()>deadline:raise RuntimeError('Cancellation did not drain')
    time.sleep(.5)
   report['post_cancel']=http('/v1/completions',payload(prompts[0]))
   assert tokens(report['post_cancel'])==tokens(report['responses'][0])
  report['passed']=True
 except Exception as e:
  report['error']=repr(e);raise
 finally:
  try:os.kill(proc.pid,signal.SIGTERM)
  except ProcessLookupError:pass
  forced=False
  try:proc.wait(timeout=90)
  except subprocess.TimeoutExpired:forced=True;os.killpg(proc.pid,signal.SIGKILL);proc.wait()
  report['shutdown']={'returncode':proc.returncode,'forced':forced}
  report['finished']=time.time()
  fatal=any(x in (a.output/'server.log').read_text(errors='replace') for x in ['FATAL: exception not rethrown','Fatal Python error:','panicked at','Segmentation fault','core dumped'])
  report['passed']=report['passed'] and not forced and not fatal and proc.returncode in (0,143,-15)
  (a.output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
if not report['passed']:raise RuntimeError('Paired engine probe failed')
