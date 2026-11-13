#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recompute route selection, disk reload and paired native resident-output tokens."""
import argparse,hashlib,json,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case;sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def read(path):
 text=path.read_text()
 try:return json.loads(text)
 except json.JSONDecodeError:
  for i,ch in enumerate(text):
   if ch!='{' or (i and text[i-1]!='\n'):continue
   try:
    value,n=json.JSONDecoder().raw_decode(text[i:])
    if not text[i+n:].strip():return value
   except json.JSONDecodeError:pass
  raise
r=read(raw/'receipt.json');env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
expected=env['environment']['image_inventory'][binding['image']]
assert r['exit_code']==0 and r['remove_returncode']==0 and not r['remaining_container_ids']
for name,checksum in r['files'].items():assert sha(raw/name)==checksum,name
assert read(raw/'container-inspect.json')[0]['Image']==expected['config_id']
installed=read(raw/'installed.json');assert installed['source_commit']==expected['source_commit']
summary=read(raw/'routing/summary.json');report=read(raw/'routing/routing.json');native=read(raw/'native-routing.json')
assert summary['status']==report['status']=='passed' and native['passed']
assert all(not p['forced'] and p['returncode'] in (0,-15,143) for p in native['shutdown'])
assert len(native['commands'])==len(report['workers'])==2 and len(set(report['workers']))==2
transcript=[json.loads(x) for x in (raw/'routing-http.jsonl').read_text().splitlines()]
rows=[x for x in transcript if x['url'].endswith('/v1/completions')]
assert len(rows)==len(native['responses']) and all(x['status']==200 for x in rows)
actual=[];reference=[];resident=None
for row,control in zip(rows,native['responses']):
 assert {k:v for k,v in row['request'].items() if k!='nvext'}=={k:v for k,v in control['request'].items() if k!='return_token_ids'}
 assert control['worker_index']==report['workers'].index(row['response']['nvext']['worker_id']['decode_worker_id'])
 token_ids=row['response']['nvext']['completion_token_ids'];native_ids=control['response']['choices'][0]['token_ids']
 if row['response']==report['resident']:resident=native_ids
 if row['response']==report['reload']:
  assert resident is not None
  native_ids=resident
 assert token_ids and native_ids and all(isinstance(t,int) for t in token_ids+native_ids)
 actual.append(token_ids);reference.append(native_ids)
assert actual==reference
assert report['reload']['nvext']['worker_id']['decode_worker_id']==report['workers'][0]
owner=next(x for x in report['offloaded_scores']['workers'] if x['worker_id']==report['workers'][0])
assert owner['device_blocks']==0 and owner['host_pinned_blocks']==0 and owner['disk_extension_blocks']>=5
assert report['reload']['usage']['prompt_tokens_details']['cached_tokens']>=640
launches=read(raw/'routing-launches.json');worker_launches=[x for x in launches if x['label'].startswith('aggregate-')]
assert len(worker_launches)==2
tops=[];reload_checks=0
for launch in worker_launches:
 log=re.sub(r'\x1b\[[0-9;]*m','',(raw/'routing'/(launch['label']+'.log.txt')).read_text(errors='replace'))
 assert not re.search(r'Fatal Python error:|FATAL: exception not rethrown|Segmentation fault|core dumped',log)
 top={}
 for key,name in [('tp','tensor_parallel_size'),('dp','data_parallel_size'),('pp','pipeline_parallel_size')]:
  values=set(map(int,re.findall(r'\b'+name+r'=(\d+)',log)));assert len(values)==1;top[key]=next(iter(values))
 tops.append(top);reload_checks+=log.count('platform KVBM reload byte verification passed')
assert tops[0]==tops[1] and reload_checks>0
observed={**tops[0],'workers':len(worker_launches)};assert observed==binding['topology']
assets={}
for file in raw.glob('model-*.json'):
 value=read(file);assert value['checked_files']>0;assets[value['model_id']]=value['manifest_sha256']
assert set(assets)==set(binding['models'])
obs={'identity':env['identity'],'installed_versions':read(raw/'versions.json'),'execution_image':r['spec']['image_reference'],
 'observed_topology':observed,'model_manifests':{'huawei-52':assets},'completed_operations':len(actual)+len(reference),
 'resources_remaining':r['remaining_container_ids'],'dynamo':{'output_tokens':actual},'native':{'output_tokens':reference},
 'measurements':{'route_correct':{'actual':[report['reload']['nvext']['worker_id']['decode_worker_id']], 'reference':[owner['worker_id']]}},
 'provenance':{'raw_receipt_sha256':sha(raw/'receipt.json'),'runtime_source':installed['source_commit'],'reload_byte_checks':reload_checks,
 'control':'Two matching native TP1 workers. Final cache reload compared to recorded resident-prefix control with identical prefill split.'}}
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True);(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'workers':observed['workers'],'paired_requests':len(actual),'reload_byte_checks':reload_checks}))
