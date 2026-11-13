#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Require disabled network, complete artifact verification, installed wheel imports and exact image identity."""
import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case
read=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
r=read(raw/'receipt.json');v=read(raw/'imports.json');container=read(raw/'container-inspect.json')[0]
assert r['exit_code']==0 and r['remove_returncode']==0 and not r['remaining_container_ids']
for name,expected in r['files'].items():assert sha(raw/name)==expected,name
expected=env['environment']['image_inventory'][binding['image']]
assert container['Image']==expected['config_id'] and container['HostConfig']['NetworkMode']=='none'
assert read(raw/'embedded-source.json')['commit']==expected['source_commit']
assert read(raw/'entrypoint.json')['sha256']==r['spec']['entrypoint_sha256']
assert read(raw/'before.json')==read(raw/'after.json')
profile=read(raw/'profile.json');assert profile['versions']==v['versions']==env['stack']['versions']
assert all(v['imports'][n]['file'].startswith('/opt/dynamo/offline-verify/') for n in ('dynamo._core','kvbm._core','dynamo.common.platform','gpu_memory_service'))
assert any('/tmp/closure-audit.py' in cmd and 'verify' in cmd for cmd in r['commands'])
sealed=read(raw/'sealed-artifacts.json');assert any(n.startswith('wheels/') for n in sealed)
assert sha(raw/'profile.json')==sealed['profile.json']
assert sha(raw/'embedded-source.json')==sealed['SOURCE.json']
obs={'identity':env['identity'],'installed_versions':v['versions'],'execution_image':r['spec']['image_reference'],
 'observed_topology':{},'model_manifests':{'huawei-52':{}},'completed_operations':len(v['imports'])+len(sealed),
 'resources_remaining':r['remaining_container_ids'],
 'measurements':{'offline_dependency_complete':{'actual':sorted(v['imports']),'reference':['dynamo._core','dynamo.common.platform','gpu_memory_service','kvbm._core']},
                 'digest_lock_matches':{'actual':{'image_config':container['Image'],'source_commit':read(raw/'embedded-source.json')['commit'],'versions':v['versions']},
                                        'reference':{'image_config':expected['config_id'],'source_commit':expected['source_commit'],'versions':env['stack']['versions']}}},
 'provenance':{'raw_receipt_sha256':sha(raw/'receipt.json'),'network_mode':container['HostConfig']['NetworkMode'],'artifact_manifest_sha256':sha(raw/'sealed-artifacts.json')}}
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True);(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'network_mode':container['HostConfig']['NetworkMode'],'imports':list(v['imports'])}))
