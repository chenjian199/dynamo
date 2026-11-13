# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,os,subprocess,hashlib,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');out=r/'service-images-r1';out.mkdir()
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(r/'docker-client-direct')}
images=['ghcr.io/ai-dynamo/grove/grove-operator:v0.1.0-alpha.14','ghcr.io/ai-dynamo/grove/grove-initc:v0.1.0-alpha.14']
record={'started':time.time(),'images':[]}
try:
 with (out/'pull.log').open('x') as log:
  for image in images:
   subprocess.run(['docker','pull','--platform','linux/arm64',image],env=env,check=True,stdout=log,stderr=subprocess.STDOUT,timeout=600)
   v=json.loads(subprocess.check_output(['docker','inspect',image],env=env))[0];record['images'].append({k:v[k] for k in ['Id','RepoTags','RepoDigests','Architecture']})
 with (out/'grove.tar').open('wb') as f:subprocess.run(['docker','save',*images],env=env,stdout=f,check=True,timeout=600)
 record['archive_sha256']=hashlib.file_digest((out/'grove.tar').open('rb'),'sha256').hexdigest()
 with (out/'import.log').open('x') as log:subprocess.run(['k3s','ctr','-n','k8s.io','images','import','--platform','linux/arm64',str(out/'grove.tar')],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
 record['passed']=True
except BaseException as e:record['error']=repr(e);raise
finally:(out/'status.json').write_text(json.dumps(record,indent=2))
