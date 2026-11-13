#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recompute engine token, cancellation, route and rank assertions from raw outputs."""
import argparse,hashlib,json,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case

def read(path):
 text=path.read_text()
 try:return json.loads(text)
 except json.JSONDecodeError:
  for offset,char in enumerate(text):
   if char!='{' or (offset and text[offset-1]!='\n'):continue
   try:
    value,used=json.JSONDecoder().raw_decode(text[offset:])
    if not text[offset+used:].strip():return value
   except json.JSONDecodeError:pass
  raise
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
record=read(raw/'receipt.json');installed=read(raw/'installed.json')
assert record['exit_code']==0 and record['remove_returncode']==0 and not record['remaining_container_ids']
assert read(raw/'container-inspect.json')[0]['Image']==env['environment']['image_inventory'][binding['image']]['config_id']
assert installed['source_commit']==env['environment']['image_inventory'][binding['image']]['source_commit']
for name,expected in record['files'].items():assert sha(raw/name)==expected,name
case=read(raw/'case/case.json');kv=read(raw/'case/kv.json');api=read(raw/'case/api.json')
assert case['passed'] and case['shutdown_passed'] and kv['passed']
dyn=read(raw/'dynamo/result.json');native=read(raw/'native/result.json')
assert dyn['passed'] and native['passed'] and dyn['prompt_token_ids']==native['prompt_token_ids']
dtokens=[x['nvext']['completion_token_ids'] for x in dyn['responses']]
ntokens=[x['choices'][0]['token_ids'] for x in native['responses']]
assert dtokens==ntokens and all(len(x)==32 for x in dtokens),(dtokens,ntokens)
for dr,nr in zip(dyn['requests'],native['requests']):
 assert {k:v for k,v in dr.items() if k!='nvext'}=={k:v for k,v in nr.items() if k!='return_token_ids'}
log=(raw/'case/server.log').read_text(errors='replace')
top={}
for key,name in [('tp','tensor_parallel_size'),('dp','data_parallel_size'),('pp','pipeline_parallel_size')]:
 values=[int(x) for x in re.findall(r'\b'+name+r'=(\d+)',log)]
 assert values and len(set(values))==1,(name,values)
 top[key]=values[0]
if 'ep' in binding['topology']:
 # EP is derived only after the running engine explicitly confirms it enabled.
 assert re.search(r'enable_expert_parallel[=:]\s*True',log), 'Missing live EP evidence'
 top['ep']=top['tp']*top['dp']
assert top==binding['topology'],top
assets={}
for f in sorted(raw.glob('model-*.json')):
 data=read(f);assert data['checked_files']>0;assets[data['model_id']]=data['manifest_sha256']
assert set(assets)==set(binding['models'])
measurements={'stream_complete':{'actual':dyn['stream']['token_ids'],'reference':dtokens[0]},
 'cache_route_matches':{'actual':[x['response']['nvext']['worker_id']['decode_dp_rank'] for x in kv['warm']],
                        'reference':[x['nvext']['worker_id']['decode_dp_rank'] for x in kv['seeds']]},
 'native_parameters_passthrough':{'actual':top,'reference':binding['topology']},
 'rank_layout_recorded':{'actual':sorted(int(x['labels']['dp_rank']) for x in kv['capacity']), 'reference':list(range(top['dp']))}}
assert dyn['stream']['done'] and dyn['after_cancel']['values']['dynamo_component_inflight_requests']==0
assert dyn['after_cancel']['values']['dynamo_component_cancellation_total']>dyn['before_cancel']['values'].get('dynamo_component_cancellation_total',0)
if a.case=='M03-F02-C03':
 restarted=read(raw/'restarted/result.json');assert restarted['passed'] and restarted['pid']!=dyn['pid']
 measurements['restart_recovers']={'actual':[x['nvext']['completion_token_ids'] for x in restarted['responses']],'reference':dtokens}
events=[event for row in kv['events'].values() for event in row]
obs={'identity':env['identity'],'installed_versions':read(raw/'versions.json'),
 'execution_image':record['spec']['image_reference'],'observed_topology':top,'model_manifests':{'huawei-52':assets},
 'completed_operations':len(dyn['responses'])+len(native['responses'])+len(kv['warm']),
 'resources_remaining':record['remaining_container_ids'],'measurements':measurements,
 'dynamo':{'output_tokens':dtokens},'native':{'output_tokens':ntokens},
 'metrics':{'kv_event_count':events.count('BlockStored'),'eviction_event_count':events.count('AllBlocksCleared'),
            'active_requests_after_cancel':dyn['after_cancel']['values']['dynamo_component_inflight_requests']},
 'provenance':{'runtime_source':installed['source_commit'],'raw_receipt_sha256':sha(raw/'receipt.json')}}
target=attempt/'cases'/a.case;target.mkdir(parents=True,exist_ok=True)
(target/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'paired_prompts':len(dtokens),'topology':top,'events':len(events)}))
