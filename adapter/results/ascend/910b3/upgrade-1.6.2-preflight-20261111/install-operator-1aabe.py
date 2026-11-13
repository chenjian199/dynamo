# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Install the built ARM64 controller only into the owned upgrade release."""
import json
import os
import subprocess
import time
from pathlib import Path

r = Path('/data/dynamo-upgrade-1.6.2-20261111/operator-1aabe')
env = {**os.environ, 'KUBECONFIG': '/etc/rancher/k3s/k3s.yaml'}
record = {'started': time.time(), 'passed': False}
try:
    actual = json.loads((r / 'image-arm64.json').read_text())[0]
    assert actual['Architecture'] == 'arm64'
    assert 'heyi/dynamo-operator:1.6.2-heyi-upgrade-1aabe435ad-arm64' in actual['RepoTags']
    with (r / 'import-52.log').open('x') as log:
        subprocess.run(['k3s', 'ctr', '-n', 'k8s.io', 'images', 'import', '--platform', 'linux/arm64', str(r / 'dynamo-operator-m08-arm64.tar.gz')], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    h = '/root/CJ_workspace/m08-1029/linux-arm64/helm'
    chart = '/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/deploy/helm/charts/platform/components/operator'
    with (r / 'helm-upgrade.log').open('x') as log:
        subprocess.run([h, 'upgrade', 'heyi-upgrade162', chart, '-n', 'heyi-upgrade162', '--reuse-values',
            '--set', 'controllerManager.manager.image.repository=heyi/dynamo-operator',
            '--set', 'controllerManager.manager.image.tag=1.6.2-heyi-upgrade-1aabe435ad-arm64',
            '--set', 'controllerManager.manager.dgdrDefaultImage=heyi/dynamo:1.6.2-ascend0.30-1aabe435ad-tools', '--wait', '--timeout', '5m'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=360)
    pods = json.loads(subprocess.check_output(['kubectl', '-n', 'heyi-upgrade162', 'get', 'pods', '-l', 'control-plane=controller-manager', '-o', 'json'], env=env))
    (r / 'installed-pods.json').write_text(json.dumps(pods, indent=2))
    record['image_id'] = actual['Id']
    record['passed'] = True
except Exception as e:
    record['error'] = repr(e)
finally:
    record['finished'] = time.time()
    (r / 'installed-result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
