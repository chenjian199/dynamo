# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import pathlib,subprocess,json,time
name='heyi-upgrade-162-native-small-tp2-20261111'
image='sha256:74224d25260decde6abdd26c6ebc383faadbaf792115631bdbe966793742c4db'
code="""import json,time,hashlib,pathlib
from vllm import LLM,SamplingParams
model='/models/Qwen3-0.6B'
start=time.time()
engine=LLM(model=model,tensor_parallel_size=2,max_model_len=2048,gpu_memory_utilization=0.2,enforce_eager=True,trust_remote_code=True,max_num_seqs=4)
prompts=['The capital of France is','Calculate 2 + 3. Answer:']
params=SamplingParams(temperature=0,max_tokens=32,seed=42)
outputs=engine.generate(prompts,params)
rows=[{'prompt':o.prompt,'prompt_token_ids':o.prompt_token_ids,'text':o.outputs[0].text,'token_ids':o.outputs[0].token_ids,'finish_reason':o.outputs[0].finish_reason} for o in outputs]
assert len(rows)==2 and all(r['token_ids'] and r['text'].strip() for r in rows)
print('HEYI_NATIVE_RESULT='+json.dumps({'model':model,'tp':2,'max_model_len':2048,'gpu_memory_utilization':0.2,'enforce_eager':True,'elapsed_seconds':time.time()-start,'outputs':rows}),flush=True)
"""
cmd=['docker','create','--name',name,'--network','none','--shm-size','4g','--entrypoint','python3','-e','OMP_NUM_THREADS=4','-e','HF_HUB_OFFLINE=1','-e','VLLM_USE_MODELSCOPE=false','-e','ASCEND_RT_VISIBLE_DEVICES=0,1']
for d in ['davinci0','davinci1','davinci_manager','devmm_svm','hisi_hdc']:cmd+=['--device','/dev/'+d]
for src in ['/usr/local/Ascend/driver','/usr/local/dcmi','/usr/local/sbin/npu-smi']:
 if pathlib.Path(src).exists():cmd+=['--mount','type=bind,src='+src+',dst='+src+',readonly']
cmd+=['--mount','type=bind,src=/data/weight/Qwen3-0.6B,dst=/models/Qwen3-0.6B,readonly',image,'-c',code]
r=subprocess.run(cmd,capture_output=True,text=True,timeout=30);record={'argv':cmd,'create_code':r.returncode,'create_stdout':r.stdout,'create_stderr':r.stderr}
if r.returncode==0:
 cid=r.stdout.strip()
 try:
  r=subprocess.run(['docker','start','-a',cid],capture_output=True,text=True,timeout=480);record.update(stdout=r.stdout,stderr=r.stderr,attach_code=r.returncode)
  record['container_state']=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]['State']
 except subprocess.TimeoutExpired:
  record['timeout_seconds']=480
  r=subprocess.run(['docker','logs',cid],capture_output=True,text=True,timeout=15);record.update(stdout=r.stdout,stderr=r.stderr)
 finally:
  r=subprocess.run(['docker','rm','-f',cid],capture_output=True,text=True,timeout=30);record['cleanup']={'code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
print(json.dumps(record,indent=2))
