#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Normalize editable-image evidence, retaining import paths and immutable layer ancestry."""
import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case
read=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
r=read(raw/'receipt.json');v=read(raw/'dev-validation.json');container=read(raw/'container-inspect.json')[0]
assert r['exit_code']==0 and r['remove_returncode']==0 and not r['remaining_container_ids']
for name,expected in r['files'].items():assert sha(raw/name)==expected,name
expected=env['environment']['image_inventory'][binding['image']]
assert container['Image']==v['image']['id']==expected['config_id']
assert r['source_commit']==expected['source_commit']
assert v['status']=='passed' and v['runtime_layers_preserved'] and v['runtime_versions_preserved'] and v['frontend_help']
assert set(v['editable'])=={'ai-dynamo-runtime','ai-dynamo','kvbm','gpu-memory-service'}
assert all(info.get('dir_info',{}).get('editable') for info in v['editable'].values())
imports={name:str(next(parent for parent in Path(file).parents if str(parent)=='/workspace/dynamo')) for name,file in v['imports'].items()}
reference={name:'/workspace/dynamo' for name in ('dynamo._core','dynamo.common.platform','kvbm._core','gpu_memory_service')}
assert imports==reference
assert all(v['tools'].values())
obs={'identity':env['identity'],'installed_versions':read(raw/'versions.json'),
 'execution_image':r['spec']['image_reference'],'observed_topology':{},
 'model_manifests':{'huawei-52':{}},'completed_operations':len(imports)+len(v['tools']),
 'resources_remaining':r['remaining_container_ids'],
 'measurements':{'editable_imports_match_source':{'actual':imports,'reference':reference},
                 'toolchain_available':{'actual':sorted(v['tools']),'reference':['cargo','gdb','maturin','rustc','strace']}},
 'provenance':{'raw_receipt_sha256':sha(raw/'receipt.json'),'editable_source_manifest_sha256':v['source_manifest_sha256'],'runtime_source':r['source_commit']}}
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True)
(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'imports':v['imports'],'tools':v['tools']}))
