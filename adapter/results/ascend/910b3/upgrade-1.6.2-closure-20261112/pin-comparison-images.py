# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,subprocess,shlex
from pathlib import Path
root=Path(__file__).resolve().parent
for host in ['huawei-55','huawei-52']:
 rows=json.loads((root/(host+'-comparison-manifest-inventory.json')).read_text())
 code='''import subprocess,json,hashlib
rows=ROWS
records=[]
for row in rows:
 ref,_,digest=row[:3]
 raw=subprocess.check_output(['k3s','ctr','-n','k8s.io','content','get',digest])
 assert 'sha256:'+hashlib.sha256(raw).hexdigest()==digest
 manifest=json.loads(raw)
 pinned=ref.rsplit(':',1)[0]+'@'+digest
 existing=subprocess.check_output(['k3s','ctr','-n','k8s.io','images','ls','-q'],text=True).splitlines()
 if pinned not in existing:subprocess.run(['k3s','ctr','-n','k8s.io','images','tag',ref,pinned],check=True,stdout=subprocess.DEVNULL)
 cri=json.loads(subprocess.check_output(['k3s','crictl','inspecti',pinned]))['status']
 assert cri['id']==manifest['config']['digest']
 assert pinned in cri['repoDigests']
 records.append({'tag':ref,'pinned':pinned,'manifest':manifest,'cri':cri})
print(json.dumps(records,indent=2))
'''.replace('ROWS',repr(rows))
 p=subprocess.run(['ssh','-F','/var/tmp/dynamo-regression-1028/ssh-config-upgrade-fresh',host,'python3 -'],input=code,capture_output=True,text=True,timeout=120)
 (root/(host+'-comparison-pin.stderr')).write_text(p.stderr)
 if p.returncode:raise SystemExit(f'{host}: pin failed, rc={p.returncode}')
 (root/(host+'-comparison-pinned.json')).write_text(p.stdout)
 print(host,[(x['pinned'],x['cri']['id']) for x in json.loads(p.stdout)])
