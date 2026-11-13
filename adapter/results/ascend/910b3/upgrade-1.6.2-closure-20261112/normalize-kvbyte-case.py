#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recompute cross-node PD placement and successful handoff assertions."""
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
summary=read(raw/'regression/summary.json');assert summary['passed'] and summary['mode']=='pd'
dgd=read(raw/'regression/dgd.json');assert record['owned_uid']==dgd['metadata']['uid']
pods=read(raw/'regression/pods.json');roles={};top={};versions=None
for role in ('prefill','decode'):
    selected=[p for p in pods if p['metadata']['labels'].get('nvidia.com/dynamo-component')==role]
    component=next(c for c in dgd['spec']['components'] if c['name']==role)
    assert len(selected)==component['replicas']==1
    p=selected[0];uid=p['metadata']['uid'];roles[role]=p['spec']['nodeName']
    assert any(x['type']=='Ready' and x['status']=='True' for x in p['status']['conditions'])
    c=next(c for c in p['spec']['containers'] if c['name']=='main')
    assert c['image']==binding['image']
    status=next(c for c in p['status']['containerStatuses'] if c['name']=='main')
    assert status['imageID'].split('@')[-1]==binding['image'].split('@')[-1]
    device=read(raw/f'worker-{uid}-device.json');assert device['available'] and device['count']==1
    installed=read(raw/f'worker-{uid}-installed.json');assert installed['source_commit']==env['environment']['image_inventory'][binding['image']]['source_commit']
    if versions is None:versions=device['versions']
    assert versions==device['versions']
    log=(raw/f'worker-{uid}.log').read_text(errors='replace');observed={}
    for key,name in [('tp','tensor_parallel_size'),('dp','data_parallel_size'),('pp','pipeline_parallel_size')]:
        values=set(map(int,re.findall(r'\b'+name+r'=(\d+)',log)));assert len(values)==1,(name,values)
        observed[key]=values.pop()
    if top:assert top==observed
    top=observed
assert roles['prefill']!=roles['decode']
assert [roles['prefill'],roles['decode']]==binding['kubernetes_nodes']
for role in ('prefill','decode'):top[role+'_replicas']=len(summary['placement'][role])
handoff=read(raw/'handoff.json')
assert handoff['case']=='valid' and handoff['status']=='passed' and handoff['outcome']=='backend_response'
assert handoff['prefill_handoff']==handoff['decode_handoff']
tokens=[t for row in handoff['responses'] for t in row.get('token_ids',[])]
assert tokens and tokens==handoff['output_token_ids'] and all(type(t)is int and t>=0 for t in tokens)
assert handoff['responses'][-1]['finish_reason'] in ('stop','length')
assert not any(row.get('finish_reason')=='error' for row in handoff['responses'])
params=handoff['prefill_handoff']['disaggregated_params']['kv_transfer_params']
assert params and params['_dynamo_pd_contract']['platform']=='ascend'
assets={}
for f in raw.glob('model-*.json'):
    data=read(f);assert data['checked_files']>0;assets[data['model_id']]=data['manifest_sha256']
remote=read(attempt/(a.case+'-model-55.json'));assert remote['checked_files']>0
assert assets[remote['model_id']]==remote['manifest_sha256']
measurements={'placement_verified':{'actual':roles,'reference':dict(zip(('prefill','decode'),binding['kubernetes_nodes']))},
 'operator_crossnode_pd':{'actual':handoff['decode_handoff'],'reference':handoff['prefill_handoff']}}
assert not record.get('observer_cleanup_error')
assert not (raw/'observer-absence.txt').read_text().strip()
assert sha(raw/'handoff-entrypoint.py')==record['spec']['entrypoint_sha256']
transfers=sorted((raw/'kv-decode').glob('transfer-*.json'));assert transfers
producer=attempt/'cases'/a.case/'producer.bin';consumer=producer.with_name('consumer.bin');producer.parent.mkdir(parents=True,exist_ok=True)
bytes_count=0
with producer.open('xb') as pfile, consumer.open('xb') as cfile:
    for path in transfers:
        transfer=read(path);assert transfer['passed'] and transfer['native_returncode']==0
        pp=path.with_suffix('.producer.bin');cp=path.with_suffix('.consumer.bin')
        assert sha(pp)==transfer['producer_sha256'] and sha(cp)==transfer['consumer_sha256']
        assert pp.stat().st_size==cp.stat().st_size==sum(transfer['sizes'])==transfer['transferred_bytes']
        pfile.write(pp.read_bytes());cfile.write(cp.read_bytes());bytes_count+=transfer['transferred_bytes']
assert bytes_count>0
machines=[(attempt/(a.case+'-'+node+'-dmi.txt')).read_text().strip() for node in binding['nodes']]
assert all(machines) and len(set(machines))==2
measurements['transport_evidence']={'actual':{'connector':params['_dynamo_pd_contract']['connector'],'native_api':list({read(f)['native_api'] for f in transfers})},'reference':{'connector':'MooncakeConnector','native_api':['batch_transfer_sync_read']}}
obs={'identity':env['identity'],'installed_versions':versions,'execution_image':record['spec']['image_reference'],
 'observed_topology':top,'model_manifests':{'huawei-52':assets,'huawei-55':{remote['model_id']:remote['manifest_sha256']}},
 'node_machine_ids':machines,'files':{'producer':str(producer),'consumer':str(consumer)},'completed_operations':len(summary['requests'])+1,'resources_remaining':record['resources_remaining'],'measurements':measurements,
 'provenance':{'receipt_sha256':sha(raw/'receipt.json'),'remote_model_sha256':sha(attempt/(a.case+'-model-55.json'))}}
target=attempt/'cases'/a.case;target.mkdir(parents=True,exist_ok=True)
(target/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'topology':top,'placement':roles,'generated_tokens':len(tokens)}))
