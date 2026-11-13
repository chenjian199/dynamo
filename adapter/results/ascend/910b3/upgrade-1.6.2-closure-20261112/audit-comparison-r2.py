#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recompute measurements and preserve actual built source, without passing release gates."""
import hashlib,json,math
from pathlib import Path
root=Path(__file__).resolve().parent;folder=root/'comparison-r2-remote'
workload=json.loads((folder/'workload.json').read_text());rows={}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def percentile(values):
 return sorted(values)[math.ceil(len(values)*.95)-1]
for role in ['native','old_heyi','new_heyi']:
 out=folder/role;data=json.loads((out/'measure.json').read_text());requests=[v for r in data['rounds'] for v in r['requests']]
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
 expected_source={'native':None,'old_heyi':'bd801c6234aae2827042c8abf268395d4582e5eb','new_heyi':'64815280cf5ec3db7a7c0570197f585ea004d323'}[role]
 assert sources and set(sources)=={expected_source}
 expected_version='0.23.0+empty' if role=='old_heyi' else '0.30.0+empty'
 assert set(versions)=={expected_version},versions
 result=json.loads((out/'result.json').read_text());assert result['passed'] and result['cleanup_complete']
 rows[role]={'metrics':metrics,'requests':len(requests),'failed_requests':0,'actual_runtime_source':expected_source,
             'vllm':expected_version,'raw_sha256':{str(p.relative_to(folder)):sha(p) for p in out.rglob('*') if p.is_file()}}
ratios={k:rows['new_heyi']['metrics'][k]/rows['old_heyi']['metrics'][k] for k in rows['new_heyi']['metrics']}
report={'passed_measurement_checks':True,'runs':rows,'new_over_old':ratios,
 'within_frozen_performance_limits':ratios['throughput']>=.9 and ratios['ttft_p95_ms']<=1.1 and ratios['tpot_p95_ms']<=1.1,
 'release_gate_status':'pending_exact_candidate_source_binding',
 'limitation':'This measures the 64815280cf built image. Candidate 9ee6c9fa only changes workflow identity preflight; do not relabel the image source or claim the final candidate comparison gate passed.'}
(root/'comparison-r2-recomputed.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='runs'},indent=2))
