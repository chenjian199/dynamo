#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recompute measurements and preserve actual built source, without passing release gates."""
import hashlib,json,math
from pathlib import Path
root=Path(__file__).resolve().parent;folder=root/'comparison-r2-remote';new_folder=root/'comparison-2a239c2b50-remote'
workload=json.loads((folder/'workload.json').read_text());rows={}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(new_folder/'workload.json')==sha(folder/'workload.json')
assert sha(new_folder/'load.py')==sha(folder/'load.py')
def percentile(values):
 return sorted(values)[math.ceil(len(values)*.95)-1]
for role in ['native','old_heyi','new_heyi']:
 out=(new_folder if role=='new_heyi' else folder)/role;data=json.loads((out/'measure.json').read_text());requests=[v for r in data['rounds'] for v in r['requests']]
 assert data['passed'] and data['failed_requests']==0 and len(requests)==256
 assert all(not r['errors'] for r in data['rounds'])
 assert all(r['prompt_tokens']==128 and r['completion_tokens']==32 for r in requests)
 assert data['workload_sha256']==sha(folder/'workload.json')
 assert data['load_script_sha256']==sha(folder/'load.py')
 assert len(data['correctness'])==len(workload['correctness_prompts'])
 for req,expected in zip(data['correctness'],workload['correctness_prompts']):
  assert req['status']==200 and req['payload']['messages'][0]['content']==expected['prompt']
  assert expected['required_text'] in json.loads(req['body'])['choices'][0]['message']['content'].lower()
 metrics={'throughput':len(requests)/sum(r['elapsed'] for r in data['rounds']),
          'ttft_p95_ms':percentile([r['ttft_ms'] for r in requests]),
          'tpot_p95_ms':percentile([r['tpot_ms'] for r in requests])}
 for k,v in metrics.items():assert abs(v-data['metrics'][k])<1e-9
 pods=json.loads((out/'runtime-pods.json').read_text());assert all(p['spec']['nodeName']=='master' for p in pods)
 sources=[];versions=[]
 for path in out.glob('*.installed.json'):
  installed=json.loads(path.read_text());sources.append(installed['source']['commit'] if installed['source'] else None);versions.append(installed['vllm'])
 expected_source={'native':None,'old_heyi':'bd801c6234aae2827042c8abf268395d4582e5eb','new_heyi':'2a239c2b500ea7e48c86b7216b9878158db4802a'}[role]
 assert sources and set(sources)=={expected_source}
 expected_version='0.23.0+empty' if role=='old_heyi' else '0.30.0+empty'
 assert set(versions)=={expected_version},versions
 result=json.loads((out/'result.json').read_text());assert result['passed'] and result['cleanup_complete']
 rows[role]={'metrics':metrics,'requests':len(requests),'failed_requests':0,'actual_runtime_source':expected_source,
             'vllm':expected_version,'raw_sha256':{str(p.relative_to(root)):sha(p) for p in out.rglob('*') if p.is_file()}}
ratios={k:rows['new_heyi']['metrics'][k]/rows['old_heyi']['metrics'][k] for k in rows['new_heyi']['metrics']}
report={'passed_measurement_checks':True,'runs':rows,'new_over_old':ratios,
 'within_frozen_performance_limits':ratios['throughput']>=.9 and ratios['ttft_p95_ms']<=1.1 and ratios['tpot_p95_ms']<=1.1,
 'release_gate_status':'awaiting_standard_comparison_attachment',
 'limitation':'Combined Dynamo and vendor engine upgrade; old vLLM 0.23 versus new/native 0.30, not Dynamo-only performance attribution.'}
(root/'comparison-2a239c2b50-recomputed.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='runs'},indent=2))

# Put a byte-for-byte copy under the upgrade session for its independent gate.
import shutil
session=Path('/home/metax/CJ_workspace/dynamo/adapter/results/workflow/upstream/dynamo-1.6.2-ascend-scoped-20261112')
target=session/'comparison-inputs'/'2a239c2b50';target.mkdir(parents=True,exist_ok=False)
conditions={'workload_sha256':sha(folder/'workload.json'),'weights_sha256':workload['weights_sha256'],
 'hardware':{'node':workload['node'],'node_ip':workload['node_ip'],'device':workload['device'],'npus':workload['npus']},
 'parallelism':{k:workload[k] for k in ('tp','dp','pp')},
 'load':{k:workload[k] for k in ('warmup_requests','measured_requests_per_round','rounds','concurrency','input_tokens','output_tokens')},
 'sampling':{k:workload[k] for k in ('temperature','ignore_eos')},'metric_definition':workload['metric_definition']}
comparison={'runs':{},'attribution':report['limitation']}
for role,row in rows.items():
 artifacts=[];origin=(new_folder if role=='new_heyi' else folder)
 for src in [origin/'workload.json',origin/'load.py',origin/'measure.py',*(origin/role).rglob('*')]:
  if not src.is_file() or '__pycache__' in src.parts:continue
  relative=Path(role)/src.relative_to(origin);dst=target/relative;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
  assert sha(src)==sha(dst);artifacts.append({'path':str(relative),'sha256':sha(dst)})
 comparison['runs'][role]={'source_commit':row['actual_runtime_source'],'vllm':row['vllm'],'conditions':conditions,
   'metrics':row['metrics'],'correct':True,'failed_requests':row['failed_requests'],'artifacts':artifacts}
(target/'input.json').write_text(json.dumps(comparison,indent=2)+'\n')
print(json.dumps({'standard_input':str(target/'input.json')}))
