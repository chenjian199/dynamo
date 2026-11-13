#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate native allocation/copy/event measurements, preserving SDK counters."""
import argparse,hashlib,json
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
r=read(raw/'receipt.json');installed=read(raw/'installed.json');container=read(raw/'container-inspect.json')[0]
assert r['exit_code']==0 and r['remove_returncode']==0 and not r['remaining_container_ids']
expected=env['environment']['image_inventory'][binding['image']]
assert container['Image']==expected['config_id'] and installed['source_commit']==expected['source_commit']
for name,expected_sha in r['files'].items():assert sha(raw/name)==expected_sha,name
m=read(raw/'memory/native-measurements.json');source=read(raw/'memory/source.json')
assert m['platform']=='ascend' and m['device']==0 and source['source_commit']==expected['source_commit']
assert m['completed_operations']>0
counts=m['sdk_create_release_counts'];assert len(counts)==8 and counts[0]>0
assert all(counts[i]==counts[i+1] for i in range(0,8,2))
assert m['metrics']['live_allocations']==0 and m['metrics']['owned_handles_after_cleanup']==0
assert m['metrics']['wrong_device_rejections']>0
for key in ['host_device_roundtrip','event_ordering','stream_lifetime']:
 pair=m['measurements'][key];assert pair['actual'] and pair['actual']==pair['reference'],key
visible=sorted(int(Path(d['PathOnHost']).name.removeprefix('davinci')) for d in container['HostConfig']['Devices'] if Path(d['PathOnHost']).name.removeprefix('davinci').isdigit())
assert visible==[0,1]
assets={}
for f in sorted(raw.glob('model-*.json')):
 data=read(f);assert data['checked_files']>0;assets[data['model_id']]=data['manifest_sha256']
assert set(assets)==set(binding['models'])
obs={'identity':env['identity'],'installed_versions':read(raw/'versions.json'),
 'execution_image':r['spec']['image_reference'],'observed_topology':{'devices':len(visible)},
 'model_manifests':{'huawei-52':assets},'completed_operations':m['completed_operations'],
 'resources_remaining':r['remaining_container_ids'],'metrics':m['metrics'],'measurements':m['measurements'],
 'provenance':{'runtime_source':installed['source_commit'],'native_source':source['source_commit'],
 'raw_receipt_sha256':sha(raw/'receipt.json'),'sdk_create_release_counts':counts}}
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True)
(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'operations':m['completed_operations'],'counts':counts}))
