# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independently recompute the frozen three-arm comparison before workflow registration."""
import argparse
import hashlib
import json
import math
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--session', type=Path, required=True)
a = p.parse_args()
s = a.session.resolve()
r = s / 'comparison-evidence/comparison-r1'
digest = lambda f: hashlib.sha256(f.read_bytes()).hexdigest()
w = json.loads((r / 'workload.json').read_text())
summary = json.loads((r / 'result.json').read_text())
assert summary['passed'] and summary.get('finished')
conditions = {
    'workload_sha256': digest(r / 'workload.json'),
    'weights_sha256': w['weights_sha256'],
    'hardware': {'node': w['node'], 'ip': w['node_ip'], 'model': w['device'], 'npus': w['npus'], 'driver': '25.2.0'},
    'parallelism': {k: w[k] for k in ['tp', 'dp', 'pp']},
    'load': {k: w[k] for k in ['warmup_requests', 'measured_requests_per_round', 'rounds', 'concurrency', 'input_tokens', 'output_tokens', 'request_timeout_seconds']},
    'sampling': {k: w[k] for k in ['temperature', 'ignore_eos']},
    'engine_configuration': {k: w[k] for k in ['dtype', 'max_model_len', 'max_num_seqs', 'max_num_batched_tokens', 'kv_cache_memory_bytes', 'block_size', 'prefix_caching', 'enforce_eager']},
    'metric_definition': w['metric_definition'],
}
source = {'native': None, 'old_heyi': 'bd801c6234aae2827042c8abf268395d4582e5eb', 'new_heyi': '1aabe435ad56922cf7e12f92f64df2dc39531ccb'}
runs = {}
for role in ['native', 'old_heyi', 'new_heyi']:
    m = json.loads((r / role / 'measure.json').read_text())
    assert m['workload_sha256'] == conditions['workload_sha256']
    assert m['load_script_sha256'] == digest(r / 'load.py')
    assert len(m['rounds']) == w['rounds']
    assert summary['arms'][role]['cleanup_complete']
    requests = []
    for rd in m['rounds']:
        assert not rd['errors'] and len(rd['requests']) == w['measured_requests_per_round']
        assert sorted(x['sequence'] for x in rd['requests']) == list(range(w['measured_requests_per_round']))
        requests.extend(rd['requests'])
    assert all(x['prompt_tokens'] == w['input_tokens'] and x['completion_tokens'] == w['output_tokens'] for x in requests)
    for expected, actual in zip(w['correctness_prompts'], m['correctness'], strict=True):
        assert actual['status'] == 200
        assert expected['required_text'] in json.loads(actual['body'])['choices'][0]['message']['content'].lower()
    elapsed = sum(x['elapsed'] for x in m['rounds'])
    metric = {'throughput': len(requests) / elapsed}
    for key, field in [('ttft_p95_ms', 'ttft_ms'), ('tpot_p95_ms', 'tpot_ms')]:
        metric[key] = sorted(x[field] for x in requests)[math.ceil(len(requests) * .95) - 1]
    assert all(math.isclose(v, m['metrics'][k], rel_tol=1e-12) for k, v in metric.items())
    files = [r / 'workload.json', r / 'load.py', r / 'measure.py', r / 'run.py', r / 'result.json', r / (role + '.json')]
    files += [f for f in (r / role).rglob('*') if f.is_file()]
    runs[role] = {'source_commit': source[role], 'vllm': '0.23.0+empty' if role == 'old_heyi' else '0.30.0+empty', 'conditions': conditions, 'correct': True, 'failed_requests': 0, 'metrics': metric, 'artifacts': [{'path': str(f.relative_to(s)), 'sha256': digest(f)} for f in files]}
out = s / 'comparison-ascend-r1.json'
assert not out.exists()
out.write_text(json.dumps({'runs': runs, 'analysis': '256 measured requests per arm, two rounds, eight excluded warmups; nearest-rank p95 independently recomputed. Correctness scope is two semantic prompts plus exact input/output token accounting and no failed requests. No DeepSeek, tail-SLA, or production-performance claim.'}, indent=2) + '\n')
print(out)
