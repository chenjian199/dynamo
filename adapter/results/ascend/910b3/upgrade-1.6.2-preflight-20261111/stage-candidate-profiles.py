# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Copy audited profiles and deployment inputs; never turn a candidate into a release."""
import hashlib
import json
import shutil
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
package = Path('/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate')
target = package / 'profiling'
target.mkdir(exist_ok=False)
copies = {
    'profiling-r3/input.json': 'offline-input.json',
    'profiling-r3/output': 'offline-output',
    'profiling-r3/audit-result.json': 'offline-audit.json',
    'profiling-r3/online-r1.yaml': 'online-150ms.yaml',
    'profiling-r4/online-r1.yaml': 'online-75ms.yaml',
    'profiling-r4/profile-origin.json': 'profile-origin.json',
    'dgdr-r2/dgdr.json': 'dgdr-thorough.json',
    'dgdr-r2/profiler/final_config.yaml': 'dgdr-selected.yaml',
    'dgdr-r2/audit': 'dgdr-audit',
}
for source, destination in copies.items():
    src, dst = root / source, target / destination
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
shutil.copy2('/root/CJ_workspace/m08-1029/helm-v3.17.3-linux-arm64.tar.gz', package / 'helpers')
record = {
    'release_qualified': False,
    'source_commit': '1aabe435ad56922cf7e12f92f64df2dc39531ccb',
    'scope': 'Site-bound measured profiles and inputs; reprofile on another model, engine or hardware.',
    'sha256': {str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(target.rglob('*')) if p.is_file()},
}
(package / 'profiling-assets.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'files': len(record['sha256']), 'release_qualified': False}))
