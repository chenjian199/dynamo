#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bind the accepted 38-feature scope to observed immutable assets, without verdicts."""
import argparse
import copy
import json
from pathlib import Path
import yaml

p=argparse.ArgumentParser()
p.add_argument('--repo',type=Path,required=True)
p.add_argument('--inventory',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--source-commit',required=True)
a=p.parse_args();root=Path(__file__).resolve().parent
old=root.parent/'upgrade-1.6.2-preflight-20261111'
inv=json.loads(a.inventory.read_text())
roles={}
for tag,item in inv.items():
 if 'operator' in tag:role='operator'
 else:role=tag.rsplit('-',1)[-1]
 assert role in ('runtime','dev','tools','operator'),tag
 assert len(item['repo_digests'])>=1
 roles[role]={'reference':item['repo_digests'][0],**item}
assert set(roles)=={'runtime','dev','tools','operator'}
env=json.loads((root/'host-identity-environment.json').read_text())
assert len(a.source_commit)==40 and all(v['source_commit']==a.source_commit for v in roles.values())
env['image_source_root']='/data/dynamo-upgrade-1.6.2-20261111/source-'+a.source_commit[:10]
env['image_inventory']={v['reference']:v for v in roles.values()}
env['asset_probe_timeout_seconds']=1800
env['kubernetes']={'host':'huawei-52','context':'default','namespace':'heyi-upgrade162',
 'resource_key':'huawei.com/Ascend910','minimum_devices_per_node':1,
 'nodes':['master-02-45d4a763','master'],'required_apis':[],'required_resources':[]}
models=[]
for filename in ['model-small-52.json','model-qwen32-source-r2.json','model-deepseek-55.json']:
 m=json.loads((old/filename).read_text());models.append({k:m[k] for k in ('id','path','revision','manifest','manifest_sha256')})
for node in env['nodes']:
 node['models']=copy.deepcopy(models)
 if node['id']=='huawei-55':node['models'][0]['path']='/data/dynamo-regression-models/Qwen3-0.6B'
 node['runtime']='k3s';node['images']=[v['reference'] for v in roles.values()]
 node['device_paths']=['/dev/davinci'+str(i) for i in range(8)]+['/dev/davinci_manager','/dev/devmm_svm','/dev/hisi_hdc']
plan=yaml.safe_load((a.repo/'adapter/upgrades/targets/dynamo-1.6.2-ascend-scoped.yaml').read_text())
scope={'features':{},'dependency_choices':{'M08-F04':['M08-F01']}}
env['case_bindings']={}
for path in sorted((a.repo/'adapter/modules').glob('*/features/*/feature.yaml')):
 feature=yaml.safe_load(path.read_text());fid=feature['id']
 if fid in plan['deferred_features']:
  scope['features'][fid]=plan['deferred_features'][fid];continue
 scope['features'][fid]={'disposition':'required'}
 for case in feature['cases']:
  cid=case['id'];role='runtime'
  if fid in ('M02-F01','M02-F02','M04-F01','M04-F02'):role='dev'
  if fid.startswith('M09-'):role='tools'
  nodes=['huawei-52'];t={'tp':1,'dp':1,'pp':1};model='Qwen3-0.6B'
  if fid.startswith('M01-'):t['workers']=2
  if cid=='M03-F04-C01':t['tp']=4;model='Qwen3-32B'
  if cid in ('M03-F04-C02','M03-F05-C01'):t['dp']=2
  if cid=='M03-F04-C03':t.update(tp=4,pp=2);model='Qwen3-32B'
  if cid in ('M03-F04-C04','M03-F05-C02','M03-F05-C04'):
   t.update(tp=8,ep=8);model='DeepSeek-V4-Flash-w8a8-mtp'
  if fid=='M05-F03':t.update(prefill_replicas=2,decode_replicas=3)
  if fid=='M05-F04':
   t.pop('tp');t.update(prefill_tp=1,decode_tp=2,prefill_replicas=1,decode_replicas=1)
  if fid in ('M05-F05','M05-F07','M08-F02','M08-F03','M08-F06'):
   nodes=['huawei-52','huawei-55'];t.update(prefill_replicas=1,decode_replicas=1)
  if fid=='M05-F07':t.update(tp=8,ep=8);model='DeepSeek-V4-Flash-w8a8-mtp'
  if fid.startswith('M06-'):t['tp']=2
  if cid=='M06-F01-C03':t={'tp':1,'dp':1,'pp':1,'workers':2}
  if fid in ('M06-F02','M06-F03','M06-F04'):t={'ranks':2}
  if fid.startswith('M02-') or fid in ('M10-F01','M10-F03','M10-F04'):t={}
  if fid.startswith('M04-'):t={'devices':[0,1]}
  if fid=='M05-F02':t={'devices':[0,1]}
  if cid=='M08-F01-C01':t={}
  binding={'nodes':nodes,'image':roles[role]['reference'],'models':[model],'topology':t}
  if 'models' not in feature['requirements'] and not fid.startswith('M01-'):binding['models']=[]
  if fid.startswith(('M08-','M09-')) and cid!='M08-F01-C01':binding['models']=[model]
  if fid=='M10-F02':binding['models']=['Qwen3-0.6B','Qwen3-32B','DeepSeek-V4-Flash-w8a8-mtp']
  if 'kubernetes' in feature['requirements']:
   binding['kubernetes_nodes']=['master-02-45d4a763']+(['master'] if len(nodes)==2 else [])
  env['case_bindings'][cid]=binding
out=a.output;out.mkdir(parents=True,exist_ok=False)
(out/'environment.json').write_text(json.dumps(env,indent=2)+'\n')
(out/'scope.json').write_text(json.dumps(scope,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'cases':len(env['case_bindings']),'required_features':sum(v['disposition']=='required' for v in scope['features'].values()),'output':str(out)}))
