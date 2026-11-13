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
assert len(roles['prefill'])==len(roles['decode'])==1
pname=roles['prefill'][0];dname=roles['decode'][0]
assert observed[pname]['dp']==observed[dname]['dp']==1
assert observed[pname]['pp']==observed[dname]['pp']==1
top={'dp':observed[pname]['dp'],'pp':observed[pname]['pp'],'prefill_tp':observed[pname]['tp'],'decode_tp':observed[dname]['tp'], 'prefill_replicas':len(roles['prefill']),'decode_replicas':len(roles['decode'])}
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
assert a.case=='M05-F04-C01'
pairs=read(pd/'explicit-topology.json');assert pairs['status']=='passed' and len(pairs['pairs'])==1 and pairs['pairs'][0]['token_ids']
source_contract=valid['prefill_handoff']['disaggregated_params']['kv_transfer_params']['_dynamo_pd_contract']
assert source_contract['tp']==observed[pname]['tp'] and source_contract['platform']=='ascend'
native_config={}
rank_actual={}
for role in ('prefill','decode'):
 name=roles[role][0];args=workers[name]['argv'];kv=json.loads(args[args.index('--kv-transfer-config')+1]);extra=kv['kv_connector_extra_config']
 native_config[role]=extra[role]['tp_size']
 assert extra['prefill']['tp_size']==top['prefill_tp'] and extra['decode']['tp_size']==top['decode_tp']
 log=(pd/(name+'.log.txt')).read_text(errors='replace')
 assigned={int(x) for x in re.findall(r'is assigned as DP rank \d+, PP rank \d+, PCP rank \d+, TP rank (\d+)',log)}
 # vLLM suppresses some initialization INFO messages on nonleader ranks.
 # Model execution process names are independently emitted after native group setup.
 loaded={int(x) for x in re.findall(r'\(Worker[^()\n]*?TP(\d+)(?:_EP\d+)? pid=\d+\).*?(?:Starting to load model|Loading model weights took|Model runner load_model)',log)}
 rank_actual[role]=sorted(assigned|loaded)
 assert rank_actual[role]==list(range(observed[name]['tp']))
# Both decode ranks must actually perform a native transfer from the same P endpoint.
log=(pd/(dname+'.log.txt')).read_text(errors='replace')
transfers=re.findall(r'KV cache transfer for request (\S+) took [0-9.]+ ms\. local_ip \S+ local_device_id (\d+) remote_session_id (\S+)',log)
by_request={}
for request,rank,peer in transfers:by_request.setdefault((request,peer),set()).add(int(rank))
assert any(ranks==set(rank_actual['decode']) for ranks in by_request.values()),transfers
obs['measurements']['unequal_tp_native_contract']={'actual':{'prefill':observed[pname]['tp'],'decode':observed[dname]['tp']},'reference':native_config}
obs['measurements']['rank_mapping']={'actual':rank_actual,'reference':{role:list(range(size)) for role,size in native_config.items()}}
obs['provenance']['native_transfer_rank_pairs']=[{'request':req,'peer':peer,'ranks':sorted(ranks)} for (req,peer),ranks in by_request.items()]
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True);(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'requests':len(success),'topology':top}))
