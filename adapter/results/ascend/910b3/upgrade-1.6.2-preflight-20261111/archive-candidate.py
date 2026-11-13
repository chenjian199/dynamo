# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Archive an explicitly unqualified candidate after bounded test controllers finish."""
import argparse
import hashlib
import json
import shutil
import tarfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--snapshot', required=True)
args = parser.parse_args()
root = Path('/data/dynamo-upgrade-1.6.2-20261111')
package = Path('/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate')
report = {'started': time.time(), 'release_qualified': False, 'passed': False}


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


try:
    snapshot = Path(args.snapshot).resolve()
    assert snapshot.parent == root and snapshot.name.startswith('final-campaigns-')
    cases = ['profiling-r3/online-result.json', 'profiling-r4/online-result.json',
             'profiling-r5/online-result.json', 'hybrid-pd-r1/result.json',
             'pp2-final-r1/result.json', 'cluster-services/pd-cross-final-r1/result.json',
             'pd-soak-final-r1/result.json', 'cluster-services/grove-final-r1/result.json',
             'pd-split-final-r1/result.json', 'cleanup-final/result.json']
    results = {name: json.loads((root / name).read_text()) for name in cases}
    assert all('finished' in result for result in results.values()), 'A controller has not finished'
    # Keep failed attempts. A functional success never substitutes for formal release qualification.
    report['case_results'] = {name: value['passed'] for name, value in results.items()}
    soak_audit = json.loads((package / 'validation/pd-soak-final-independent-audit.json').read_text())
    assert soak_audit['passed'], 'Final-soak independent audit did not pass'
    assert digest(package / 'validation/pd-soak-final-55.tar.gz') == soak_audit['archive_sha256']
    assert results['cleanup-final/result.json']['passed'], 'Owned-resource cleanup incomplete'
    with tarfile.open(snapshot, 'r:gz') as archive:
        hashes = json.load(archive.extractfile('RAW_SHA256.json'))
        for name, expected in hashes.items():
            actual = hashlib.sha256(archive.extractfile(name).read()).hexdigest()
            assert actual == expected, name
    shutil.copy2(snapshot, package / 'validation' / snapshot.name)
    for name in ['grove-final-r1.json', 'deepseek-final-r2.json', 'pp2-new.json']:
        shutil.copy2(root / name, package / 'manifests' / name)
    for name, destination in {'online-r1.yaml': 'online-75ms-down95.yaml',
                              'profile-origin.json': 'profile-origin-down95.json'}.items():
        shutil.copy2(root / 'profiling-r5' / name,
                     package / 'profiling' / destination)
    profile_index = package / 'profiling-assets.json'
    shutil.copy2(profile_index, package / 'validation/profiling-assets.initial.json')
    profiles = json.loads(profile_index.read_text())
    profiles['sha256'] = {str(p.relative_to(package)): digest(p)
                          for p in sorted((package / 'profiling').rglob('*')) if p.is_file()}
    profile_index.write_text(json.dumps(profiles, indent=2) + '\n')
    for name in ['upgrade-summary.md', 'upgrade-gate-final.json']:
        shutil.copy2(root / name, package / 'validation' / name)
    candidate = json.loads((package / 'candidate.json').read_text())
    candidate.update(status='archived_candidate_not_release_qualified', release_qualified=False,
                     validation_snapshot=snapshot.name, validation_snapshot_sha256=digest(snapshot),
                     case_results=report['case_results'])
    (package / 'candidate.json').write_text(json.dumps(candidate, indent=2) + '\n')
    entries = []
    for path in sorted(package.rglob('*')):
        assert not path.is_symlink(), 'Candidate package must contain actual files: ' + str(path)
        if path.is_file() and path.name != 'SHA256SUMS':
            entries.append(digest(path) + '  ' + str(path.relative_to(package)))
    (package / 'SHA256SUMS').write_text('\n'.join(entries) + '\n')
    output = Path(str(package) + '.tar')
    with tarfile.open(output, 'x') as archive:
        archive.add(package, arcname=package.name)
    report.update(passed=True, archive=str(output), bytes=output.stat().st_size,
                  sha256=digest(output), files=len(entries), directory=str(package))
except Exception as error:
    report['error'] = repr(error)
finally:
    report['finished'] = time.time()
    (root / 'candidate-archive-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
