#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recompute cache-tier hits and paired native token parity from actual HTTP and SDK logs."""
import argparse,hashlib,json,re
from pathlib import Path
from prometheus_client.parser import text_string_to_metric_families
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
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
base=read(raw/'baseline/summary.json');cache=read(raw/'cache/summary.json')
assert base['status']==cache['status']=='passed' and cache['baseline_output_parity']
assert len(base['responses'])==len(cache['responses'])==6
rows={arm:[json.loads(line) for line in (raw/(arm+'-http.jsonl')).read_text().splitlines()] for arm in ('baseline','cache')}
assert all(len(v)==6 for v in rows.values())
assert all(x['status']==200 and not x['stream'] for values in rows.values() for x in values)
for left,right in zip(rows['baseline'],rows['cache']):assert left['request']==right['request']
# Final reload is compared to a resident-prefix hit with the same BF16 prefill
# split. This mapping is fixed by the canonical harness, not selected by output.
references=rows['baseline'].copy();references[-1]=references[1]
actual=[row['response']['choices'][0]['token_ids'] for row in rows['cache']]
reference=[row['response']['choices'][0]['token_ids'] for row in references]
assert all(len(tokens)==32 and all(isinstance(t,int) for t in tokens) for tokens in actual+reference)
assert actual==reference,(actual,reference)
log=re.sub(r'\x1b\[[0-9;]*m','',(raw/'cache/engine.log.txt').read_text(errors='replace'))
assert not re.search(r'Fatal Python error:|FATAL: exception not rethrown|Segmentation fault|core dumped',log)
checks=log.count('platform KVBM reload byte verification passed');assert checks>0 and checks==cache['reload_byte_checks']
directions=re.findall(r'platform KVBM transfer completed source=(\w+) target=(\w+) blocks=(\d+)',log)
source='Disk' if a.case=='M06-F01-C02' else 'Host'
hit_blocks=sum(int(n) for src,dst,n in directions if src==source and dst=='Device');assert hit_blocks>0
if source=='Disk':assert any(src=='Host' and dst=='Disk' and int(n)>0 for src,dst,n in directions)
def hits(file):
 return sum(s.value for f in text_string_to_metric_families(file.read_text()) for s in f.samples if s.name=='vllm:external_prefix_cache_hits_total')
assert hits(raw/'cache/after.metrics.txt')-hits(raw/'cache/before.metrics.txt')==cache['external_cache_hits']>0
top={}
for key,name in [('tp','tensor_parallel_size'),('dp','data_parallel_size'),('pp','pipeline_parallel_size')]:
 values=set(map(int,re.findall(r'\b'+name+r'=(\d+)',log)));assert len(values)==1;top[key]=next(iter(values))
assert top==binding['topology']
assets={}
for file in raw.glob('model-*.json'):
 value=read(file);assert value['checked_files']>0;assets[value['model_id']]=value['manifest_sha256']
assert set(assets)==set(binding['models'])
obs={'identity':env['identity'],'installed_versions':read(raw/'versions.json'),'execution_image':r['spec']['image_reference'],
 'observed_topology':top,'model_manifests':{'huawei-52':assets},'completed_operations':len(actual)+len(reference),
 'resources_remaining':r['remaining_container_ids'],'dynamo':{'output_tokens':actual},'native':{'output_tokens':reference},
 'metrics':{('disk_hit_count' if source=='Disk' else 'host_hit_count'):hit_blocks},
 'provenance':{'raw_receipt_sha256':sha(raw/'receipt.json'),'runtime_source':installed['source_commit'],'reload_byte_checks':checks,
               'native_reference_indices':[0,1,2,3,4,1],'reference_reason':'Same resident-prefix prefill split as the fixed canonical cache harness.'}}
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True);(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'tier':source,'hit_blocks':hit_blocks,'paired_requests':len(actual),'reload_byte_checks':checks}))
