# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Retry the recorded isolated tools build with cached downloads."""
import json, os, subprocess, time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111'); w=r/'candidate-5cbe'
a=json.loads((w/'tools-build-r3-start.json').read_text())['argv']
assert a[:3]==['docker','buildx','build'] and a[-1]==str(w/'tools-context')
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(r/'docker-client-direct')}
record={'argv':a,'started':time.time(),'attempt':4}
(w/'tools-build-r4-start.json').write_text(json.dumps(record,indent=2))
with (w/'tools-build-r4.log').open('x') as f:
 record['exit_code']=subprocess.run(a,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=5400).returncode
record['finished']=time.time()
if record['exit_code']==0:
 tag='heyi/dynamo:1.6.2-ascend0.30-5cbe424879-tools'
 info=json.loads(subprocess.check_output(['docker','image','inspect',tag],env=env))[0]
 record['image']={'tag':tag,'id':info['Id']}
(w/'tools-build-r4.json').write_text(json.dumps(record,indent=2))
