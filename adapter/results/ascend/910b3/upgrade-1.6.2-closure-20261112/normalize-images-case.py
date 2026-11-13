#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independently check installed imports, layer ancestry and isolated tools paths."""
import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def read(path):
 text=path.read_text();decoder=json.JSONDecoder()
 try:return json.loads(text)
 except json.JSONDecodeError:
  for i,ch in enumerate(text):
   if ch=='{' and (i==0 or text[i-1]=='\n'):
    try:
     value,used=decoder.raw_decode(text[i:])
     if not text[i+used:].strip():return value
    except json.JSONDecodeError:pass
  raise

env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
r=read(raw/'receipt.json');dev=read(raw/'dev-validation.json');tools=read(raw/'tools-isolation.json')
assert r['exit_code']==0 and r['remove_returncode']==0 and not r['remaining_container_ids']
assert set(r['extra_cleanup'])=={'runtime','tools'}
assert all(x['returncode']==0 and not x['remaining_ids'] for x in r['extra_cleanup'].values())
for name,expected in r['files'].items():assert sha(raw/name)==expected,name
inventory=r['spec']['images'];runtime=read(raw/'runtime-installed.json');toolruntime=read(raw/'tools-installed.json')
for role,observed in [('runtime',runtime),('tools',toolruntime)]:
 container=read(raw/(role+'-inspect.json'))[0]
 assert container['Image']==inventory[role]['config_id']
 assert observed['passed'] and observed['source_commit']==inventory[role]['source_commit']
 assert all(x['path'].startswith('/opt/dynamo/venv/') for x in observed['modules'].values())
assert read(raw/'container-inspect.json')[0]['Image']==inventory['dev']['config_id']==dev['image']['id']
assert dev['status']=='passed' and dev['runtime_layers_preserved'] and dev['runtime_versions_preserved']
assert dev['image']['runtime_id']==inventory['runtime']['config_id']
assert read(raw/'tools-runtime-before.json')==read(raw/'tools-runtime-after.json')
assert tools['prefix']=='/opt/dynamo/tools-venv'
assert all(path.startswith('/opt/dynamo/tools-venv/') for path in tools['modules'].values())
assert not any('/opt/dynamo/venv/' in path or '/usr/local/python3.12.16/lib/python3.12/site-packages' in path for path in tools['paths'])
assert (raw/'tools-pip-check.txt').read_text().strip()=='No broken requirements found.'
versions=read(raw/'versions.json');assert versions==env['stack']['versions']
modules={n:'wheel' if '/site-packages/' in info['path'] else 'source' for n,info in runtime['modules'].items()}
ref={n:'wheel' for n in ('dynamo._core','dynamo.vllm','dynamo.common.platform','kvbm._core','gpu_memory_service')}
obs={'identity':env['identity'],'installed_versions':versions,'execution_image':r['spec']['image_reference'],
 'observed_topology':{},'model_manifests':{'huawei-52':{}},'completed_operations':len(modules)+len(tools['modules']),
 'resources_remaining':r['remaining_container_ids'],
 'measurements':{'runtime_installed_imports':{'actual':modules,'reference':ref},
 'dev_derives_runtime':{'actual':{'runtime_id':dev['image']['runtime_id'],'layers_preserved':dev['runtime_layers_preserved']},'reference':{'runtime_id':inventory['runtime']['config_id'],'layers_preserved':True}},
 'tools_isolation':{'actual':{n:tools['prefix'] for n,path in tools['modules'].items() if path.startswith(tools['prefix']+'/')},'reference':{n:'/opt/dynamo/tools-venv' for n in ('dynamo.profiler','dynamo.planner','aisimulate','aiperf')}}},
 'provenance':{'raw_receipt_sha256':sha(raw/'receipt.json'),'runtime_source':r['source_commit'],'image_inventory':inventory}}
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True);(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'installed_modules':modules,'tools_prefix':tools['prefix']}))
