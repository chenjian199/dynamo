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
byte=read(raw/'rdma/byte-summary.json');assert byte['passed'] and len(byte['ranks'])==2
expected_cases=[{'operation':op,'bytes':size,'iteration':iteration} for iteration in range(2) for op in ('READ','WRITE') for size in (4,4096,524288,4194304,16777216)]
selected=read(raw/'rdma/byte-pods.json');assert len({p['spec']['nodeName'] for p in selected})==2
native_bytes={};native_calls=[]
for rank,arm in enumerate(byte['ranks']):
    assert arm['rank']==rank and arm['cases']==expected_cases and arm['device_allocated_after']==0 and arm['passed']
    log=(raw/f'rdma/byte-rank-{rank}.log').read_text(errors='replace')
    audits=[json.loads(line.split('=',1)[1]) for line in log.splitlines() if line.startswith('NATIVE_TRANSPORT_AUDIT=')]
    assert len(audits)==1
    audit=audits[0]
    assert audit['probe_sha256']==record['spec']['byte_probe_sha256']
    assert 'install AscendDirectTransport' in log and 'install TcpTransport' not in log
    initialized={}
    for call in audit['calls']:
        assert call['returncode']==0
        if call['method']=='initialize':
            assert len(call['args'])>=3
            initialized[call['engine']]=call['args'][2]
        else:
            assert call['engine'] in initialized
            protocol=initialized[call['engine']]
            native_bytes[protocol]=native_bytes.get(protocol,0)+sum(call['sizes'])
            native_calls.append({'operation':call['method'].removeprefix('batch_transfer_sync_').upper(),'bytes':sum(call['sizes'])})
    assert set(initialized.values())=={'ascend'}
assert native_calls==[{'operation':case['operation'],'bytes':case['bytes']} for case in expected_cases]
assert native_bytes.get('ascend',0)>0
counters={};roce_octets={}
for node in binding['nodes']:
    phases={phase:read(attempt/(a.case+'-'+node+'-'+phase+'.json')) for phase in ('idle','before','after')}
    assert len({v['host'] for v in phases.values()})==1
    deltas={};total=0;roce_packets=0
    for dev,before in phases['before']['devices'].items():
        idle=phases['idle']['devices'][dev];after=phases['after']['devices'][dev]
        delta={key:after[key]-value for key,value in before.items()}
        assert all(v>=0 for v in delta.values()),'Counter reset during run'
        assert before['roce_tx_rc_pkt_num']==idle['roce_tx_rc_pkt_num'] and before['roce_rx_rc_pkt_num']==idle['roce_rx_rc_pkt_num'],'Non-idle RoCE baseline'
        if delta['roce_tx_rc_pkt_num'] or delta['roce_rx_rc_pkt_num']:
            octets=delta['mac_tx_total_oct_num']-delta['nic_tx_all_oct_num']
            assert octets>0
            total+=octets;roce_packets+=delta['roce_tx_rc_pkt_num']
        assert all(value==0 for key,value in delta.items() if key.endswith('_err_pkt_num')),delta
        deltas[dev]=delta
    assert total>0 and roce_packets>0
    counters[node]=deltas;roce_octets[node]=total
measurements['byte_parity']={'actual':byte['ranks'][1]['cases'],'reference':byte['ranks'][0]['cases']}
obs={'identity':env['identity'],'installed_versions':versions,'execution_image':record['spec']['image_reference'],
 'observed_topology':top,'model_manifests':{'huawei-52':assets,'huawei-55':{remote['model_id']:remote['manifest_sha256']}},
 'metrics':{'rdma_bytes_delta':min(roce_octets.values())},'transport':{'rdma_bytes':native_bytes['ascend'],'tcp_fallback_bytes':sum(value for key,value in native_bytes.items() if key=='tcp')},'completed_operations':len(summary['requests'])+1+len(native_calls),'resources_remaining':record['resources_remaining'],'measurements':measurements,
 'provenance':{'roce_mac_octets_by_node':roce_octets,'counter_definition':'MAC TX octets minus non-RoCE NIC TX octets; includes frame overhead, not an application throughput measure','native_transport_bytes':native_bytes,'receipt_sha256':sha(raw/'receipt.json'),'remote_model_sha256':sha(attempt/(a.case+'-model-55.json'))}}
target=attempt/'cases'/a.case;target.mkdir(parents=True,exist_ok=True)
(target/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'case':a.case,'topology':top,'placement':roles,'generated_tokens':len(tokens)}))
