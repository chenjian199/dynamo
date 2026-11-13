#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recompute SDK reclamation and all rank readback parity from actual binary files."""
import argparse
import hashlib
import json
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
summary=read(raw/'native/summary.json');assert summary['status']=='passed'
assert summary['devices']==['0','1']
assert len(summary['cases'])==6
actual,reference,shapes,expected_shapes=[],[],[],[]
replicated,replicated_ref=[],[]
live,operations=0,0
producer=bytearray();consumer=bytearray()
for outer in (1,2):
 for width in (2,4,8):
  folder=raw/'native'/f'outer-{outer}-width-{width}'
  rank_shards=[];tier_hashes=[]
  for rank in (0,1):
   m=read(folder/f'rank-{rank}.measurements.json')
   assert (m['rank'],m['world'],m['device'],m['outer'],m['width'])==(rank,2,rank,outer,width)
   counts=m['sdk_create_release_counts']
   assert len(counts)==8 and counts[0]>0
   assert all(counts[i]==counts[i+1] for i in range(0,8,2))
   live+=m['metrics']['live_allocations'];operations+=m['completed_operations']
   expected_size=3*outer*4*16*width
   shapes.append([m['rank'],m['world'],m['outer'],m['width'],m['shard_bytes']])
   expected_shapes.append([rank,2,outer,width,expected_size])
   for kind in ('shard','G2','G3'):
    af=folder/(f'rank-{rank}.shard-actual.bin' if kind=='shard' else f'rank-{rank}.{kind}.actual.bin')
    rf=folder/(f'rank-{rank}.shard-reference.bin' if kind=='shard' else f'rank-{rank}.reference.bin')
    assert len(af.read_bytes())==expected_size and len(rf.read_bytes())==expected_size
    actual.append(sha(af));reference.append(sha(rf))
    if kind=='shard':rank_shards.append(sha(af))
    if kind=='G3':
     tier_hashes.append(sha(af));replicated.append(sha(af));replicated_ref.append(sha(folder/'rank-0.reference.bin'))
     producer.extend((folder/'rank-0.reference.bin').read_bytes());consumer.extend(af.read_bytes())
  assert len(set(rank_shards))==2 and len(set(tier_hashes))==1
assert actual==reference and replicated==replicated_ref and producer==consumer
assets={}
for f in sorted(raw.glob('model-*.json')):
 data=read(f);assert data['checked_files']>0;assets[data['model_id']]=data['manifest_sha256']
assert set(assets)==set(binding['models'])
target=attempt/'cases'/a.case;target.mkdir(parents=True,exist_ok=True)
(target/'producer.bin').write_bytes(producer);(target/'consumer.bin').write_bytes(consumer)
obs={'identity':env['identity'],'installed_versions':read(raw/'versions.json'),
 'execution_image':record['spec']['image_reference'],'observed_topology':{'ranks':len(summary['devices'])},
 'model_manifests':{'huawei-52':assets},'completed_operations':operations,
 'resources_remaining':record['remaining_container_ids'],'metrics':{'live_allocations':live},
 'measurements':{'engine_physical_chain':{'actual':actual,'reference':reference},
                 'per_rank_roundtrip':{'actual':actual,'reference':reference},
                 'shard_layout':{'actual':shapes,'reference':expected_shapes},
                 'replicated_broadcast':{'actual':replicated,'reference':replicated_ref}},
 'files':{'producer':str((target/'producer.bin').relative_to(attempt)),'consumer':str((target/'consumer.bin').relative_to(attempt))},
 'provenance':{'runtime_source':installed['source_commit'],'raw_receipt_sha256':sha(raw/'receipt.json'),'byte_pairs':len(actual)}}
(target/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'byte_pairs':len(actual),'operations':operations,'live_allocations':live}))
