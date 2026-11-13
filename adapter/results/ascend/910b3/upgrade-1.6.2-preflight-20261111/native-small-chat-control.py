# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Native chat-template control for a model-quality observation, not an API benchmark."""
import json
from vllm import LLM,SamplingParams
llm=LLM(model='/models/Qwen3-0.6B',tensor_parallel_size=1,enforce_eager=True,max_model_len=1024,max_num_seqs=8,gpu_memory_utilization=0.3,block_size=128,enable_prefix_caching=True)
t=llm.get_tokenizer();rows=[]
for p in ['What is 2+3? Answer only the number.','What is 2 plus 3? Answer with a single digit.','What is the capital of France? Answer with the city name only.']:
 ids=t.apply_chat_template([{'role':'user','content':p}],tokenize=True,add_generation_prompt=True,enable_thinking=False)
 result=llm.generate([{'prompt_token_ids':ids}],SamplingParams(temperature=0,max_tokens=64))[0]
 rows.append({'prompt':p,'prompt_token_ids':ids,'text':result.outputs[0].text,'token_ids':result.outputs[0].token_ids})
print('NATIVE_CONTROL='+json.dumps(rows),flush=True)
