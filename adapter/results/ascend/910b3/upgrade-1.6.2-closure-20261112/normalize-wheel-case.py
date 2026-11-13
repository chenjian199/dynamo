#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify observed imports against the newly built wheel bytes and vendor versions."""
import argparse
import hashlib
import json
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case
read=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
r=read(raw/'receipt.json');v=read(raw/'imports.json');container=read(raw/'container-inspect.json')[0]
assert r['exit_code']==0 and r['remove_returncode']==0 and not r['remaining_container_ids']
for name,expected in r['files'].items():
    if name not in r['remote_only_files']:assert sha(raw/name)==expected,name
expected=env['environment']['image_inventory'][binding['image']]
assert container['Image']==expected['config_id']
assert r['source_commit']==expected['source_commit']
profile=read(raw/'artifacts/profile.json')
before=read(raw/'before.json')['packages'];after=read(raw/'after.json')['packages']
protected_before={n:before.get(n) for n in profile['protected_packages']}
protected_after={n:after.get(n) for n in profile['protected_packages']}
assert protected_before==protected_after
symbols={'dynamo._core':'DistributedRuntime','kvbm._core':'BlockManager'}
actual={};reference={}
for module,symbol in symbols.items():
    info=v['modules'][module]
    assert info['symbol']==symbol and info['file'].startswith('/opt/dynamo/closure-verify/')
    relative=info['file'].split('/site-packages/',1)[1]
    wheel=v['wheel_modules'][relative]
    assert 'artifacts/wheels/'+wheel['wheel'] in r['remote_only_files']
    actual[module]=info['sha256'];reference[module]=wheel['sha256']
assert actual==reference and len(actual)==2
assert v['versions']==read(raw/'versions.json')==env['stack']['versions']
obs={'identity':env['identity'],'installed_versions':v['versions'],
 'execution_image':r['spec']['image_reference'],'observed_topology':{},
 'model_manifests':{'huawei-52':{}},'completed_operations':len(actual)+len(protected_before),
 'resources_remaining':r['remaining_container_ids'],
 'measurements':{'protected_packages_unchanged':{'actual':protected_after,'reference':protected_before},
                 'rust_python_imports':{'actual':actual,'reference':reference}},
 'provenance':{'raw_receipt_sha256':sha(raw/'receipt.json'),'runtime_source':r['source_commit'],
               'remote_wheels':r['spec']['output']+'/artifacts/wheels',
               'collection':'Metadata and import hashes collected; complete wheels remain under remote repository results.'}}
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True)
(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'modules':v['modules'],'protected_packages':len(protected_before)}))
