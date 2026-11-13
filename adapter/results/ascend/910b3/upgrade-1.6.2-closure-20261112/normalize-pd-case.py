#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recompute PD layout, worker-pair coverage and bounded recovery from original observations."""
import argparse,hashlib,json,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case;pd=raw/'pd'
read=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
r=read(raw/'receipt.json');container=read(raw/'container-inspect.json')[0];expected=env['environment']['image_inventory'][binding['image']]
assert r['exit_code']==0 and r['remove_returncode']==0 and not r['remaining_container_ids']
for name,checksum in r['files'].items():assert sha(raw/name)==checksum,name
assert container['Image']==expected['config_id']
# check-installed may prepend third-party diagnostics before the final JSON.
text=(raw/'installed.json').read_text();installed=None
for i,ch in enumerate(text):
 if ch=='{' and (i==0 or text[i-1]=='\n'):
  try:
   obj,n=json.JSONDecoder().raw_decode(text[i:])
   if not text[i+n:].strip():installed=obj;break
  except json.JSONDecodeError:pass
assert installed and installed['source_commit']==expected['source_commit']
s=read(pd/'summary.json');assert s['status']=='passed'
exits=[json.loads(x) for x in (pd/'process-exits.jsonl').read_text().splitlines()]
assert exits and all(x['returncode'] in (0,-15,143) for x in exits),exits
for file in pd.glob('*.log.txt'):
 assert not re.search(r'Fatal Python error:|FATAL: exception not rethrown|Segmentation fault|core dumped',file.read_text(errors='replace')),str(file)
valid=read(pd/'valid-handoff.json');assert valid['status']=='passed' and valid['case']=='valid' and valid['output_token_ids']
assert valid['prefill_handoff']==valid['decode_handoff']
launches=read(pd/'observed-launches.json');workers={x['display_name']:x for x in launches if x['display_name'].startswith(('prefill','decode'))}
roles={role:[k for k in workers if k.split('-')[0]==role] for role in ('prefill','decode')}
assert all(roles.values())
observed={};rank_groups={}
for role,item in workers.items():
 log=(pd/(role+'.log.txt')).read_text(errors='replace')
 observed[role]={}
 for key,name in [('tp','tensor_parallel_size'),('dp','data_parallel_size'),('pp','pipeline_parallel_size')]:
  values=set(map(int,re.findall(r'\b'+name+r'=(\d+)',log)));assert len(values)==1,(role,name,values)
  observed[role][key]=next(iter(values))
 devices=item['environment']['ASCEND_RT_VISIBLE_DEVICES'].split(',');assert len(devices)==observed[role]['tp']
 rank_groups[role]=devices
assert len({d for values in rank_groups.values() for d in values})==sum(map(len,rank_groups.values()))
assert len({tuple(x.values()) for x in observed.values()})==1,'Unequal-TP needs dedicated rank-map evidence before normalization'
top=dict(next(iter(observed.values())))
if 'prefill_replicas' in binding['topology']:top.update(prefill_replicas=len(roles['prefill']),decode_replicas=len(roles['decode']))
assert top==binding['topology']
assets={}
for file in raw.glob('model-*.json'):
 x=read(file);assert x['checked_files']>0;assets[x['model_id']]=x['manifest_sha256']
assert set(assets)==set(binding['models'])
http=[json.loads(line) for line in (pd/'http-responses.jsonl').read_text().splitlines()]
success=[x for x in http if not x['stream'] and x['status']==200 and x.get('response',{}).get('choices')]
assert success and all(x['status']==200 for x in http)
reference=s['responses'][0]['choices']
assert all(x['response']['choices']==reference for x in success)
obs={'identity':env['identity'],'installed_versions':read(raw/'versions.json'),'execution_image':r['spec']['image_reference'],
 'observed_topology':top,'model_manifests':{'huawei-52':assets},'completed_operations':len(success),'resources_remaining':r['remaining_container_ids'],
 'measurements':{},'metrics':{},'provenance':{'raw_receipt_sha256':sha(raw/'receipt.json'),'runtime_source':installed['source_commit'],'actual_worker_devices':rank_groups}}
if a.case=='M05-F01-C01':
 bad=read(pd/'handoff.json');assert bad['case']=='mismatch' and bad['status']=='passed'
 original=bad['prefill_handoff']['disaggregated_params']['kv_transfer_params']['_dynamo_pd_contract']
 changed=bad['decode_handoff']['disaggregated_params']['kv_transfer_params']['_dynamo_pd_contract']
 assert {k for k in original if original[k]!=changed.get(k)}=={'block_size'} and changed['block_size']==original['block_size']+1
 assert 'Incompatible PD peer' in (bad.get('error','')+json.dumps(bad['responses']))
 assert s['handoff_recovery']=='passed'
 obs['measurements']['layout_metadata_preserved']={'actual':valid['decode_handoff']['disaggregated_params'],'reference':valid['prefill_handoff']['disaggregated_params']}
 obs['metrics']['invalid_layout_rejections']=1
elif a.case=='M05-F03-C01':
 pairs=read(pd/'topology.json');assert pairs['status']=='passed'
 actual=sorted([x['prefill'],x['decode']] for x in pairs['pairs'])
 expected_pairs=sorted([p,d] for p in pairs['producers'] for d in pairs['consumers'])
 assert actual==expected_pairs and len(actual)==6
 assert len(pairs['producers'])==len(roles['prefill'])==2 and len(pairs['consumers'])==len(roles['decode'])==3
 assert all(x['token_ids']==pairs['pairs'][0]['token_ids'] and x['token_ids'] for x in pairs['pairs'])
 obs['topology']={'prefill_replicas':len(roles['prefill']),'decode_replicas':len(roles['decode'])}
 obs['measurements']['all_workers_observed']={'actual':actual,'reference':expected_pairs}
elif a.case.startswith('M05-F06-'):
 soak=read(pd/'soak.json');assert soak['status']=='passed' and soak['elapsed']>0
 assert soak['completed']<=len(success) and soak['cancellations']>0
 cancellations=[x for x in http if x['stream']];assert len(cancellations)>=soak['cancellations']
 recovered=[]
 for cancelled in cancellations:
  after=[x for x in success if x['started_ns']>cancelled['finished_ns']]
  if after:recovered.append(min(after,key=lambda x:x['started_ns'])['response']['choices'])
 assert recovered
 obs['measurements']['cancel_recovers']={'actual':recovered,'reference':[reference for _ in recovered]}
 faults=list(pd.glob('soak-handoff-*.json'));assert len(faults)==soak['faults']
 if a.case!='M05-F06-C01':
  assert faults
  for file in faults:
   f=read(file);assert f['case']=='unreachable' and f['status']=='passed' and f['outcome']!='client_timeout_without_backend_completion'
  exits=[json.loads(x) for x in (pd/'process-exits.jsonl').read_text().splitlines()]
  recovery=[]
  for fault in faults:
   exit=next(x for x in exits if x['label']==fault.stem);assert exit['returncode']==0
   after=[x for x in success if x['started_ns']>exit['finished_ns']];assert after
   recovery.append(min(after,key=lambda x:x['started_ns'])['response']['choices'])
  obs['measurements']['fault_recovers']={'actual':recovery,'reference':[reference for _ in recovery]}
 samples=soak['samples'][2:];assert samples
 handles=[];fds=[]
 for sample in samples:
  assert all(v==0 for role in sample['idle'].values() for k,v in role.items() if k in ('vllm:num_requests_running','vllm:num_requests_waiting','vllm:kv_cache_usage_perc'))
  assert sample['native']
  assert all(n['active'].get(k,0)==0 for n in sample['native'].values() for k in ('transfers','sync_calls'))
  handles.append(sum(sum(n['active'].values()) for n in sample['native'].values()))
  fds.append(sum(n['fds'] for n in sample['processes'].values()))
 obs.update(duration_seconds=soak['elapsed'],requests_completed=soak['completed'],resource_samples={'live_handles':handles,'file_descriptors':fds})
else:raise NotImplementedError('This case needs additional rank-map/cross-node measurements')
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True);(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'requests':len(success),'topology':top}))
