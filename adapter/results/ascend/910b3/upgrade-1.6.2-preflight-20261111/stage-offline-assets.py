# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stage source, tested site inputs and optional cluster dependencies without release sealing."""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

r = Path('/data/dynamo-upgrade-1.6.2-20261111')
s = Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111')
out = Path('/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate')
h = '/root/CJ_workspace/m08-1029/linux-arm64/helm'
record = {'started': time.time(), 'passed': False, 'release_qualified': False}
try:
    for directory in ['charts', 'manifests', 'helpers', 'dependencies']:
        (out / directory).mkdir(exist_ok=False)
    subprocess.run([h, 'package', str(s / 'deploy/helm/charts/platform/components/operator'), '--destination', str(out / 'charts')], check=True, timeout=60)
    for src in [r / 'grove-charts-v0.1.0-alpha.14.tgz', Path('/root/CJ_workspace/m08-1029/kai-scheduler-v0.17.0.tgz')]:
        shutil.copy2(src, out / 'charts' / src.name)
    shutil.copy2(r / 'service-images-r1/grove.tar', out / 'dependencies/grove.tar')
    for src in Path('/root/CJ_workspace/m08-1029/kai-arm64').glob('*.tar'):
        shutil.copy2(src, out / 'dependencies' / ('kai-' + src.name))
    subprocess.run(['python3', str(s / 'adapter/modules/m10-validation/common/offline/dependency-inventory.py'), '--directory', str(out / 'dependencies'), '--arch', 'arm64', '--output', str(out / 'dependencies/inventory.json')], check=True, timeout=1200)
    for src in ['qwen32-final-r1.json', 'deepseek-final-r1.json', 'pd-cross-final-dgd.json', 'hybrid-pd-r1.json']:
        shutil.copy2(r / src, out / 'manifests' / src)
    for src in ['grove-values.json', 'kai-values.json', 'grove-crds.yaml']:
        shutil.copy2(r / 'cluster-services' / src, out / 'manifests' / src)
    env = {**os.environ, 'KUBECONFIG': '/etc/rancher/k3s/k3s.yaml'}
    values = subprocess.check_output([h, 'get', 'values', 'heyi-upgrade162', '-n', 'heyi-upgrade162', '-o', 'json'], env=env)
    (out / 'manifests/operator-values.json').write_bytes(values)
    for src in ['import.sh', 'verify-images.py', 'image_helpers.py', 'offline-usage.md']:
        shutil.copy2(s / 'adapter/modules/m10-validation/common/offline' / src, out / 'helpers' / src)
    # This exact, hash-audited source snapshot contains no .git or environment secrets.
    assert not (s / '.git').exists()
    with tarfile.open(out / 'source-1aabe435ad.tar.gz', 'x:gz') as archive:
        archive.add(s, arcname='dynamo')
    record.update(passed=True, source_commit='1aabe435ad56922cf7e12f92f64df2dc39531ccb')
except Exception as error:
    record['error'] = repr(error)
finally:
    record['finished'] = time.time()
    (out / 'staging.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
