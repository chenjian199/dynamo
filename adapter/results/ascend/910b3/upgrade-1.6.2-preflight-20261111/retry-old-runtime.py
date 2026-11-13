# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build the unchanged H context with the daemon's local image-ID builder."""
import json,os,subprocess,time,hashlib
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'baseline-h';c=w/'runtime-context'
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(r/'docker-client-direct'),'DOCKER_BUILDKIT':'0'}
base='sha256:660ce23a83574fdcd28ba126b07615829c06770c06fbdf6533f03cc4d90acdb1';tag='heyi/dynamo:1.5.1-ascend0.23-baseline-bd801c6234-runtime'
assert json.loads(subprocess.check_output(['docker','image','inspect',base],env=env))[0]['Id']==base
a=['docker','build','--network','host','--build-arg','BASE_IMAGE='+base,'--build-arg','ACCELERATOR=ascend','-f',str(c/'runtime.Dockerfile'),'-t',tag,str(c)]
record={'argv':a,'context_sha256':{str(p.relative_to(c)):hashlib.sha256(p.read_bytes()).hexdigest() for p in c.rglob('*') if p.is_file()},'started':time.time()}
(w/'runtime-r2-start.json').write_text(json.dumps(record,indent=2))
with (w/'runtime-r2.log').open('x') as f:record['exit_code']=subprocess.run(a,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=1800).returncode
if record['exit_code']==0:record['image']={'tag':tag,'id':json.loads(subprocess.check_output(['docker','image','inspect',tag],env=env))[0]['Id']}
record['finished']=time.time();(w/'runtime-r2.json').write_text(json.dumps(record,indent=2))
