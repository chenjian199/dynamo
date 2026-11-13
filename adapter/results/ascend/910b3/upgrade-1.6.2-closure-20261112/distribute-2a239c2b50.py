#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Export completed owned candidates and import exact bytes on the controller."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

root=Path('/data/dynamo-upgrade-1.6.2-20261111')
work=root/'candidate-2a239c2b50';source=root/'source-2a239c2b50'
out=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/images-2a239c2b50')
out.mkdir(parents=True,exist_ok=False)
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],
     'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(root/'docker-client-direct')}
record={'started':time.time(),'status':'waiting_for_builds'}
(out/'status.json').write_text(json.dumps(record,indent=2)+'\n')
try:
    deadline=time.monotonic()+7200
    while True:
        if (work/'images.json').exists():
            images=json.loads((work/'images.json').read_text())
            if all('id' in images.get(k,{}) for k in ('runtime','dev','tools')):break
            if any(images.get(k,{}).get('exit_code',0)!=0 for k in ('dev','tools')):raise RuntimeError(images)
        if time.monotonic()>deadline:raise TimeoutError('Images did not complete')
        time.sleep(10)
    refs=[images[k]['tag'] for k in ('runtime','dev','tools')]
    refs+=['heyi/dynamo-operator:1.6.2-heyi-upgrade-2a239c2b50-arm64']
    record['images']=images;record['status']='exporting'
    (out/'status.json').write_text(json.dumps(record,indent=2)+'\n')
    script=source/'adapter/modules/m10-validation/common/offline/export-images.py'
    with (out/'export.log').open('x') as f:
        subprocess.run(['python3',str(script),'--output',str(out/'export'),*refs],env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=5400)
    archive=out/'export/images.tar.gz'
    with (out/'import-52.log').open('x') as f:
        subprocess.run(['k3s','ctr','-n','k8s.io','images','import','--platform','linux/arm64',str(archive)],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=1800)
    inv={}
    for ref in refs:
        cri=json.loads(subprocess.check_output(['k3s','crictl','inspecti',ref]))
        status=cri['status']
        rows=subprocess.check_output(['k3s','ctr','-n','k8s.io','images','ls'],text=True).splitlines()
        canonical=ref if ref.startswith('docker.io/') else 'docker.io/'+ref
        row=next(x.split() for x in rows if x.split()[0] in (ref,canonical))
        manifest_digest=row[2]
        assert manifest_digest.startswith('sha256:') and len(manifest_digest)==71
        pinned=canonical.rsplit(':',1)[0]+'@'+manifest_digest
        subprocess.run(['k3s','ctr','-n','k8s.io','images','tag',canonical,pinned],check=True)
        cri=json.loads(subprocess.check_output(['k3s','crictl','inspecti',pinned]))
        status=cri['status']; digests=status['repoDigests']
        assert pinned in digests,(pinned,digests)
        docker=json.loads(subprocess.check_output(['/data/dynamo-m10/docker-bin/docker','image','inspect',ref],env=env))[0]
        assert docker['Id']==status['id'],(docker['Id'],status['id'])
        inv[ref]={'config_id':status['id'],'repo_digests':digests,'source_commit':images['source_commit']}
        (out/(ref.rsplit('-',1)[-1]+'-cri.json')).write_text(json.dumps(cri,indent=2)+'\n')
    (out/'inventory.json').write_text(json.dumps(inv,indent=2)+'\n')
    record['status']='controller_imported';record['inventory']=inv
except Exception as e:
    record['status']='failed';record['error']=repr(e)
finally:
    record['finished']=time.time()
    (out/'status.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record),flush=True)
