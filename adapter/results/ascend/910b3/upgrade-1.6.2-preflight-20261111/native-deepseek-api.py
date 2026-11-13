# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded native CLI API control, retaining every response and cleanup result."""
import concurrent.futures,json,os,subprocess,time,urllib.request
from pathlib import Path
out=Path('/evidence');out.mkdir(exist_ok=True)
args=['python3','-m','vllm.entrypoints.openai.api_server','--model','/models/DeepSeek-V4-Flash-w8a8-mtp','--served-model-name','upgrade-deepseek','--host','0.0.0.0','--port','18031','--tensor-parallel-size','8','--enable-expert-parallel','--enforce-eager','--max-model-len','4096','--max-num-batched-tokens','4096','--max-num-seqs','4','--gpu-memory-utilization','0.9','--block-size','128','--tokenizer-mode','deepseek_v4','--quantization','ascend','--no-enable-prefix-caching','--additional-config','{"enable_dsa_cp":true}']
(out/'argv.json').write_text(json.dumps(args,indent=2));rows=[];report={'started':time.time(),'kind':'native-vllm-cli','passed':False}
with (out/'server.log').open('x') as log:
 server=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:
  deadline=time.monotonic()+1200
  while True:
   assert server.poll() is None,'Native engine exited'
   try:
    with urllib.request.urlopen('http://127.0.0.1:18031/health',timeout=3) as f:
     if f.status==200:break
   except Exception:pass
   if time.monotonic()>deadline:raise TimeoutError('Native readiness timeout')
   time.sleep(5)
  def request(index):
   payload={'model':'upgrade-deepseek','prompt':'The capital of France is','temperature':0,'seed':42,'max_tokens':32}
   req=urllib.request.Request('http://127.0.0.1:18031/v1/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
   start=time.time()
   with urllib.request.urlopen(req,timeout=180) as f:response=json.load(f)
   return {'index':index,'request':payload,'response':response,'latency_s':time.time()-start}
  for batch in [1,1,1,1,1,1,4,4,4,4]:
   start=time.time()
   with concurrent.futures.ThreadPoolExecutor(max_workers=batch) as pool:result=list(pool.map(request,range(batch)))
   rows.append({'concurrency':batch,'wall_s':time.time()-start,'requests':result})
   (out/'responses.json').write_text(json.dumps(rows,indent=2))
  unique={r['response']['choices'][0]['text'] for batch in rows for r in batch['requests']}
  assert all(r['response']['usage']['completion_tokens']>0 for batch in rows for r in batch['requests'])
  report.update(passed=True,requests=sum(len(b['requests']) for b in rows),unique_outputs=len(unique),deterministic=len(unique)==1,scope='API liveness and repeatability control, not an SLO or native-vs-Dynamo performance certification')
  with urllib.request.urlopen('http://127.0.0.1:18031/metrics',timeout=30) as f:(out/'metrics.txt').write_bytes(f.read())
 except BaseException as error:
  report['error']=repr(error);raise
 finally:
  import signal
  if server.poll() is None:os.killpg(server.pid,signal.SIGTERM)
  try:server.wait(timeout=60)
  except subprocess.TimeoutExpired:os.killpg(server.pid,signal.SIGKILL);server.wait(timeout=30)
  report.update(finished=time.time(),server_exit=server.returncode)
  (out/'result.json').write_text(json.dumps(report,indent=2))
