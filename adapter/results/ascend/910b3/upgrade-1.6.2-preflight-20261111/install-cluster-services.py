# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Upgrade orphaned test-owned Grove/KAI components after an empty-resource guard."""
import json,os,subprocess,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'cluster-services';env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'};h='/root/CJ_workspace/m08-1029/linux-arm64/helm';record={'started':time.time(),'steps':[]}
def run(argv,name):
 with (w/name).open('x') as log:p=subprocess.run(argv,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=700)
 record['steps'].append({'argv':argv,'exit_code':p.returncode,'log':name});(w/'result.json').write_text(json.dumps(record,indent=2));p.check_returncode()
try:
 for resource in ['podcliquesets.grove.io','podcliques.grove.io','podcliquescalinggroups.grove.io','podgangs.scheduler.grove.io']:
  x=json.loads(subprocess.check_output(['kubectl','get',resource,'-A','-o','json'],env=env));assert not x['items'],resource
 roles=json.loads(subprocess.check_output(['kubectl','get','clusterroles','-o','json'],env=env))
 for obj in roles['items']:
  n=obj['metadata']['name']
  if n.startswith(('grove:','kai-')):
   a=obj['metadata'].get('annotations',{});assert a.get('meta.helm.sh/release-name') in ['heyi-grove','heyi-kai'],n
 run(['kubectl','apply','--server-side','--force-conflicts','--field-manager=heyi-upgrade162','-f',str(w/'grove-crds.yaml')],'grove-crds-apply.log')
 run([h,'upgrade','--install','heyi-grove',str(r/'grove-charts-v0.1.0-alpha.14.tgz'),'-n','grove-system','--create-namespace','-f',str(w/'grove-values.json'),'--wait','--timeout','8m'],'grove-install.log')
 run([h,'upgrade','--install','heyi-kai','/root/CJ_workspace/m08-1029/kai-scheduler-v0.17.0.tgz','-n','kai-scheduler','--create-namespace','-f',str(w/'kai-values.json'),'--wait','--timeout','8m'],'kai-install.log')
 record['passed']=True
finally:
 record['finished']=time.time();(w/'result.json').write_text(json.dumps(record,indent=2))
