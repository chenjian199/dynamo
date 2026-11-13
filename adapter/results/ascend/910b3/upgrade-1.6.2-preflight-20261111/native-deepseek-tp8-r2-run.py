# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import pathlib,subprocess,json,time
name='heyi-upgrade-162-native-deepseek-tp8-r2-20261111'
image='sha256:74224d25260decde6abdd26c6ebc383faadbaf792115631bdbe966793742c4db'
code='#!/usr/bin/env python3\n# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\n# SPDX-License-Identifier: Apache-2.0\n"""Frozen Ascend DeepSeek engine-only repeatability control (no Dynamo)."""\n\nimport importlib.metadata\nimport json\n\nfrom vllm import LLM, SamplingParams\n\n\ndef main():\n    llm = LLM(\n        model="/models/DeepSeek-V4-Flash-w8a8-mtp",\n        tensor_parallel_size=8,\n        enable_expert_parallel=True,\n        enforce_eager=True,\n        max_model_len=4096,\n        max_num_batched_tokens=4096,\n        max_num_seqs=4,\n        gpu_memory_utilization=0.9,\n        block_size=128,\n        tokenizer_mode="deepseek_v4",\n        quantization="ascend",\n        enable_prefix_caching=False,\n        additional_config={"enable_dsa_cp": True},\n    )\n    sampling = SamplingParams(temperature=0, seed=42, max_tokens=16)\n    rows = []\n    for batch in (1, 1, 1, 1, 1, 1, 4, 4, 4, 4):\n        outputs = llm.generate(["The capital of France is"] * batch, sampling)\n        rows.append(\n            {\n                "batch": batch,\n                "outputs": [\n                    {"text": r.outputs[0].text, "token_ids": r.outputs[0].token_ids}\n                    for r in outputs\n                ],\n            }\n        )\n    unique = {tuple(x["token_ids"]) for r in rows for x in r["outputs"]}\n    print(\n        "REGRESSION_RESULT="\n        + json.dumps(\n            {\n                "passed": len(unique) == 1,\n                "unique_outputs": len(unique),\n                "cases": rows,\n                "versions": {\n                    n: importlib.metadata.version(n)\n                    for n in ("vllm", "vllm-ascend", "torch-npu")\n                },\n                "qualification": "Native LLM diagnostic without Dynamo; not an identical API scheduling claim",\n            }\n        ),\n        flush=True,\n    )\n\n\nif __name__ == "__main__":\n    main()\n'
cmd=['docker','create','--name',name,'--network','host','--shm-size','32g','--entrypoint','python3','-e','OMP_NUM_THREADS=4','-e','HF_HUB_OFFLINE=1','-e','VLLM_USE_MODELSCOPE=false','-e','ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7']
for setting in ['OMP_PROC_BIND=false','PYTORCH_NPU_ALLOC_CONF=expandable_segments:True','HCCL_BUFFSIZE=1024','TASK_QUEUE_ENABLE=1','HCCL_OP_EXPANSION_MODE=AIV','HCCL_CONNECT_TIMEOUT=300','HCCL_EXEC_TIMEOUT=300']:cmd+=['-e',setting]
for d in ['davinci0','davinci1','davinci2','davinci3','davinci4','davinci5','davinci6','davinci7','davinci_manager','devmm_svm','hisi_hdc']:cmd+=['--device','/dev/'+d]
for src in ['/usr/local/Ascend/driver','/usr/local/dcmi','/usr/local/sbin/npu-smi']:
 if pathlib.Path(src).exists():cmd+=['--mount','type=bind,src='+src+',dst='+src+',readonly']
cmd+=['--mount','type=bind,src=/data/DeepSeek-V4-Flash-w8a8-mtp,dst=/models/DeepSeek-V4-Flash-w8a8-mtp,readonly',image,'-c',code]
r=subprocess.run(cmd,capture_output=True,text=True,timeout=30);record={'argv':cmd,'create_code':r.returncode,'create_stdout':r.stdout,'create_stderr':r.stderr}
if r.returncode==0:
 cid=r.stdout.strip()
 try:
  r=subprocess.run(['docker','start','-a',cid],capture_output=True,text=True,timeout=900);record.update(stdout=r.stdout,stderr=r.stderr,attach_code=r.returncode)
  record['container_state']=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]['State']
 except subprocess.TimeoutExpired:
  record['timeout_seconds']=900
  r=subprocess.run(['docker','logs',cid],capture_output=True,text=True,timeout=15);record.update(stdout=r.stdout,stderr=r.stderr)
 finally:
  r=subprocess.run(['docker','rm','-f',cid],capture_output=True,text=True,timeout=30);record['cleanup']={'code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
print(json.dumps(record,indent=2))
