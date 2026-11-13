# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Use build-scoped IPv4 DNS overrides when the site's IPv6 route blackholes."""
import json,os,signal,socket,subprocess,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'candidate-5cbe';proc=Path('/proc/3148113')
a=[x.decode() for x in (proc/'cmdline').read_bytes().split(b'\0')[:-1]]
assert a[:3]==['docker','buildx','build'] and str(w/'tools-context')==a[-1]
record={'prior_pid':3148113,'prior_proc_stat':(proc/'stat').read_text(),'prior_argv':a.copy(),'started':time.time()}
os.kill(3148113,signal.SIGTERM)
for _ in range(60):
 if not proc.exists():break
 time.sleep(1)
assert not proc.exists()
record['dns']={}
for host in ('pypi.tuna.tsinghua.edu.cn','pypi.org','files.pythonhosted.org'):
 ip=socket.getaddrinfo(host,443,socket.AF_INET)[0][4][0];record['dns'][host]=ip;a[-1:-1]=['--add-host',host+':'+ip]
record['argv']=a
(w/'tools-build-r3-start.json').write_text(json.dumps(record,indent=2)+'\n')
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(r/'docker-client-direct')}
with (w/'tools-build-r3.log').open('x') as f:rc=subprocess.run(a,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=5400).returncode
record['exit_code']=rc
if rc==0:
 tag='heyi/dynamo:1.6.2-ascend0.30-5cbe424879-tools';info=json.loads(subprocess.check_output(['docker','image','inspect',tag],env=env))[0];record['image']={'tag':tag,'id':info['Id']}
(w/'tools-build-r3.json').write_text(json.dumps(record,indent=2)+'\n')
raise SystemExit(rc)
