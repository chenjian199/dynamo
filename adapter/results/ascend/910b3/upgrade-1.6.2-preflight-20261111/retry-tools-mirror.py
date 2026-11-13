# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Retry a task-owned stalled dependency build with an explicit package index."""
import json,os,signal,subprocess,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'candidate-5cbe'
old=json.loads((w/'descendant-processes.json').read_text())['tools'];pid=old['pid']
proc=Path(f'/proc/{pid}')
record={'prior':old,'mirror':'https://pypi.tuna.tsinghua.edu.cn/simple','started':time.time()}
if proc.exists():
    actual=(proc/'cmdline').read_bytes().split(b'\0')[:-1]
    assert [x.decode() for x in actual]==old['argv']
    record['prior_proc_stat']=(proc/'stat').read_text()
    os.kill(pid,signal.SIGTERM)
    for _ in range(60):
        if not proc.exists():break
        time.sleep(1)
    assert not proc.exists(),'previous build still active'
(w/'tools-build-mirror-retry.json').write_text(json.dumps(record,indent=2)+'\n')
a=old['argv'].copy();a[-1:-1]=['--build-arg','PIP_INDEX_URL='+record['mirror']]
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(r/'docker-client-direct')}
with (w/'tools-build-r2.log').open('x') as log:
    rc=subprocess.run(a,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=5400).returncode
record['argv']=a;record['exit_code']=rc
if rc==0:
    tag='heyi/dynamo:1.6.2-ascend0.30-5cbe424879-tools'
    image=json.loads(subprocess.check_output(['docker','image','inspect',tag],env=env))[0]
    record['image']={'tag':tag,'id':image['Id']}
(w/'tools-build-r2.json').write_text(json.dumps(record,indent=2)+'\n')
raise SystemExit(rc)
