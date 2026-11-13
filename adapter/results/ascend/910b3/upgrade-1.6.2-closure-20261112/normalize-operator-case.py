#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify raw Webhook rejections and live SDK/device allocations independently."""
import argparse,hashlib,json,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case

def read(path):
    text=path.read_text()
    try:return json.loads(text)
    except json.JSONDecodeError:
        for i,ch in enumerate(text):
            if ch!='{' or (i and text[i-1]!='\n'):continue
            try:
                v,n=json.JSONDecoder().raw_decode(text[i:])
                if not text[i+n:].strip():return v
            except json.JSONDecodeError:pass
        raise
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
record=read(raw/'receipt.json');assert record['exit_code']==0 and not record['resources_remaining'] and not record.get('cleanup_error')
for name,expected in record['files'].items():assert sha(raw/name)==expected,name
manifest=read(raw/'manifest.json');measures={};metrics={};assets={};versions=None;completed=0;top={}
if a.case.endswith('C01'):
    rows=read(raw/'admission.json');assert len(rows)==6
    for row in rows:assert row['returncode']!=0 and row['expected'].lower() in row['output'].lower(),row
    metrics['admission_rejections']=len(rows);completed=len(rows)
    versions=read(raw/'versions.json')
    resources={k for c in manifest['spec']['components'] for k in c['podTemplate']['spec']['containers'][0]['resources']['limits'] if '/' in k}
    node=next(n for n in read(raw/'nodes.json')['items'] if n['metadata']['name']==binding['kubernetes_nodes'][0])
    actual={k:int(node['status']['capacity'][k]) for k in resources}
    reference={k:int(node['status']['allocatable'][k]) for k in resources}
    assert resources=={'huawei.com/Ascend910'} and actual['huawei.com/Ascend910']>0
    measures['resource_key']={'actual':actual,'reference':reference}
else:
    summary=read(raw/'regression/summary.json');assert summary['passed']
    assert {'initial','scale-3','scale-2','worker-recreated','recovery-api','scale-zero'} <= {s['name'] for s in summary['steps']}
    snapshots=list(raw.glob('worker-*-device.json'));assert len(snapshots)>=2
    actual={};reference={}
    for f in snapshots:
        uid=f.name.removeprefix('worker-').removesuffix('-device.json');device=read(f);pod=read(raw/f'worker-{uid}-pod.json')
        installed=read(raw/f'worker-{uid}-installed.json')
        assert installed['source_commit']==env['environment']['image_inventory'][binding['image']]['source_commit']
        assert device['available'] and device['count']==1 and '910' in device['name'],device
        c=next(c for c in pod['spec']['containers'] if c['name']=='main');status=next(c for c in pod['status']['containerStatuses'] if c['name']=='main')
        assert c['image']==binding['image'] and status['ready']
        assert status['imageID'].split('@')[-1]==binding['image'].split('@')[-1],status
        assert pod['spec']['nodeName'] in binding['kubernetes_nodes']
        actual[uid]=device['count'];reference[uid]=int(c['resources']['limits']['huawei.com/Ascend910'])
        if versions is None:versions=device['versions']
        assert versions==device['versions']
        log=(raw/f'worker-{uid}.log').read_text(errors='replace')
        observed={}
        for key,name in [('tp','tensor_parallel_size'),('dp','data_parallel_size'),('pp','pipeline_parallel_size')]:
            values=set(map(int,re.findall(r'\b'+name+r'=(\d+)',log)));assert len(values)==1,(name,values)
            observed[key]=values.pop()
        if top:assert top==observed
        top=observed
    measures['pod_device_visible']={'actual':actual,'reference':reference}
    for f in raw.glob('model-*.json'):
        data=read(f);assert data['checked_files']>0;assets[data['model_id']]=data['manifest_sha256']
    completed=len(summary['steps'])
obs={'identity':env['identity'],'installed_versions':versions,'execution_image':record['spec']['image_reference'],
     'observed_topology':top,'model_manifests':{'huawei-52':assets},'completed_operations':completed,
     'resources_remaining':record['resources_remaining'],'measurements':measures,'metrics':metrics,
     'provenance':{'receipt_sha256':sha(raw/'receipt.json'),'operator_deployment_sha256':sha(raw/'operator.json')}}
target=attempt/'cases'/a.case;target.mkdir(parents=True,exist_ok=True)
(target/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'completed_operations':completed}))
