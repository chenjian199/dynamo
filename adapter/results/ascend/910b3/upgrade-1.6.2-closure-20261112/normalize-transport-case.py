#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compare independently captured source/target GPU bytes and successful native transfer calls."""
import argparse,hashlib,json,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
attempt=a.attempt.resolve();raw=attempt/'raw'/a.case
read=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
env=read(attempt/'envelope.json');binding=env['environment']['case_bindings'][a.case]
r=read(raw/'receipt.json');container=read(raw/'container-inspect.json')[0];expected=env['environment']['image_inventory'][binding['image']]
assert r['exit_code']==0 and r['remove_returncode']==0 and not r['remaining_container_ids']
for name,checksum in r['files'].items():assert sha(raw/name)==checksum,name
assert container['Image']==expected['config_id']
installed=read(raw/'installed.json');assert installed['source_commit']==expected['source_commit']
s=read(raw/'transport/summary.json');assert s['status']=='passed' and s['exit_codes']==[0,0] and not s['fault'] and s['iterations']==64
protocols=[];operations=0
for rank in (0,1):
 result=read(raw/'transport'/f'rank-{rank}.json');calls=read(raw/'transport'/f'rank-{rank}.transport-calls.json')
 assert result['status']=='passed' and len(result['cycles'])==64
 assert all(x['allocated']==0 for x in result['cycles'])
 init=[x for x in calls if x['method']=='initialize'];assert len(init)==1 and init[0]['return']==0
 protocols.append(init[0]['arguments'][2])
 registered=[x for x in calls if x['method']=='register_memory'];released=[x for x in calls if x['method']=='unregister_memory']
 assert len(registered)==len(released)==64
 assert all(x['return']==0 for x in registered+released)
 assert all(x['arguments'][1]==4*1024**2 for x in registered)
 transfers=[x for x in calls if x['method']=='batch_transfer_sync_read']
 assert len(transfers)==(64 if rank==1 else 0)
 assert all(x['return']==0 and x['arguments'][3]==[4*1024**2] for x in transfers)
 operations+=len(transfers)
 file=raw/'transport'/f'rank-{rank}.device-readback.bin'
 assert file.stat().st_size==result['byte_capture']['bytes']==4*1024**2 and sha(file)==result['byte_capture']['sha256']
assert protocols==['ascend','ascend']
producer=raw/'transport/rank-0.device-readback.bin';consumer=raw/'transport/rank-1.device-readback.bin'
assert producer.read_bytes()==consumer.read_bytes() and set(producer.read_bytes())=={0,42}
visible=sorted(int(Path(x['PathOnHost']).name.removeprefix('davinci')) for x in container['HostConfig']['Devices'] if Path(x['PathOnHost']).name.removeprefix('davinci').isdigit())
assets={}
for f in raw.glob('model-*.json'):
 x=read(f);assert x['checked_files']>0;assets[x['model_id']]=x['manifest_sha256']
assert set(assets)==set(binding['models'])
out=attempt/'cases'/a.case;out.mkdir(parents=True,exist_ok=True)
for name,file in [('producer',producer),('consumer',consumer)]:shutil.copyfile(file,out/(name+'.bin'))
obs={'identity':env['identity'],'installed_versions':read(raw/'versions.json'),'execution_image':r['spec']['image_reference'],
 'observed_topology':{'devices':len(visible)},'model_manifests':{'huawei-52':assets},'completed_operations':operations,'resources_remaining':r['remaining_container_ids'],
 'files':{name:str((out/(name+'.bin')).relative_to(attempt)) for name in ('producer','consumer')},
 'measurements':{'actual_transport_evidence':{'actual':{'protocols':protocols,'successful_reads':operations},'reference':{'protocols':['ascend','ascend'],'successful_reads':64}}},
 'provenance':{'raw_receipt_sha256':sha(raw/'receipt.json'),'runtime_source':installed['source_commit'],'instrumentation':'Transparent TransferEngine call tracing; actual initialization and synchronous device-copy returns. Not a performance benchmark.'}}
(out/'observations.json').write_text(json.dumps(obs,indent=2)+'\n');print(json.dumps({'case':a.case,'reads':operations,'byte_parity':producer.stat().st_size}))
