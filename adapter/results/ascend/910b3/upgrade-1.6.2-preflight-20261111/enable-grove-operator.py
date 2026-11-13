# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the upstream live Grove compatibility check before enabling integration."""
import json,os,runpy,subprocess
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'cluster-services';env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'}
assert json.loads((w/'result.json').read_text())['passed']
check=runpy.run_path(str(w/'grove-preflight.py'))['check']
def get(path):
 x=json.loads(subprocess.check_output(['kubectl','get','--raw',path],env=env));(w/'grove-live-deployment.json').write_text(json.dumps(x,indent=2));return x
check(get,'v0.1.0-alpha.14','grove-system','grove-operator')
h='/root/CJ_workspace/m08-1029/linux-arm64/helm';chart='/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/deploy/helm/charts/platform/components/operator'
with (w/'operator-enable.log').open('x') as f:
 subprocess.run([h,'upgrade','heyi-upgrade162',chart,'-n','heyi-upgrade162','--reuse-values','--set','global.grove.enabled=true','--set','global.kai-scheduler.enabled=true','--set','controllerManager.manager.dgdrDefaultImage=heyi/dynamo:1.6.2-ascend0.30-49ef509336-tools','--wait','--timeout','5m'],env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=400)
subprocess.run(['kubectl','label','namespace','heyi-upgrade162','heyi.io/kai-test=true','--overwrite'],env=env,check=True)
