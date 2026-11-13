# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independently recheck archived bounded-soak observations and raw-file digests."""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
report = {'passed': False, 'release_qualified': False, 'archive': str(args.archive)}


def digest(stream):
    checksum = hashlib.sha256()
    for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
        checksum.update(chunk)
    return checksum.hexdigest()


try:
    with args.archive.open('rb') as stream:
        report['archive_sha256'] = digest(stream)
    with tarfile.open(args.archive, 'r:gz') as archive:
        hashes = json.load(archive.extractfile('RAW_SHA256.json'))
        for name, expected in hashes.items():
            with archive.extractfile(name) as stream:
                assert digest(stream) == expected, name
        soak = json.load(archive.extractfile('results/soak.json'))
    assert soak['status'] == 'passed'
    assert soak['elapsed'] >= 1800 and soak['completed'] >= 1000
    assert soak['faults'] == 3 and soak['cancellations'] > 0
    samples = soak['samples']
    assert len(samples) >= 3
    assert all(a['requests'] < b['requests'] and a['elapsed'] < b['elapsed']
               for a, b in zip(samples, samples[1:]))
    assert samples[-1]['requests'] == soak['completed']
    assert len(samples) == soak['cancellations']
    for sample in samples:
        assert set(sample['idle']) == {'prefill', 'decode'}
        for metrics in sample['idle'].values():
            for key in ['vllm:num_requests_running', 'vllm:num_requests_waiting', 'vllm:kv_cache_usage_perc']:
                assert metrics[key] == 0, (sample['requests'], key, metrics[key])
        for native in sample['native'].values():
            assert native['active'].get('transfers', 0) == 0
            assert native['active'].get('sync_calls', 0) == 0
    baseline, final = samples[2], samples[-1]
    assert set(baseline['processes']) == set(final['processes'])
    growth = {pid: {key: final['processes'][pid][key] - before[key]
                    for key in ['rss', 'fds', 'threads']}
              for pid, before in baseline['processes'].items()}
    for delta in growth.values():
        assert delta['rss'] <= 256 * 1024 ** 2 and delta['fds'] <= 8 and delta['threads'] <= 4
    assert set(baseline['native']) == set(final['native'])
    for key in baseline['native']:
        assert baseline['native'][key]['active'] == final['native'][key]['active'], key
    report.update(passed=True, raw_files=len(hashes), elapsed=soak['elapsed'],
                  completed=soak['completed'], cancellations=soak['cancellations'],
                  faults=soak['faults'], samples=len(samples), resource_growth=growth,
                  scope='Bounded30minute lifecycle/resource regression, not unbounded leak-freedom or production SLA')
except Exception as error:
    report['error'] = repr(error)
args.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
raise SystemExit(0 if report['passed'] else 1)
