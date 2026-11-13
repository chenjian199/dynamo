# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate Python and Rust editable installs without altering the frozen snapshot."""
import json,os,shutil,subprocess,time,hashlib
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');s=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111');out=r/'candidate-49ef/dev-editable-r1';out.mkdir();source=out/'source';shutil.copytree(s,source)
manifest=json.loads((source/'SOURCE.json').read_text());assert manifest['commit']=='49ef509336de2c6573dcffa018b33ed864d0c050'
for p,h in manifest['files'].items():assert hashlib.sha256((source/p).read_bytes()).hexdigest()==h,p
image=json.loads((r/'candidate-49ef/images.json').read_text())['dev'];env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock'}
args=['docker','run','-d','--name','heyi-upgrade162-dev-49ef-r1','--network','host','--user','0','-e','CARGO_BUILD_JOBS=8','-e','CARGO_HOME=/build-cache/cargo','-e','RUSTUP_HOME=/build-cache/rustup','-e','CARGO_TARGET_DIR=/build-cache/target','-e','CARGO_NET_OFFLINE=true','--mount',f'type=bind,src={r/"cache"},dst=/build-cache','--mount',f'type=bind,src={source},dst=/workspace/dynamo','--mount','type=bind,src=/usr/local/Ascend/driver,dst=/usr/local/Ascend/driver,readonly',image['id'],'sleep','infinity']
record={'started':time.time(),'image':image,'source_commit':manifest['commit'],'argv':args};cid=None
try:
 cid=subprocess.check_output(args,env=env,text=True).strip();record['container_id']=cid;(out/'ownership.json').write_text(json.dumps(record,indent=2))
 record['modes']={}
 for mode in ['python','all']:
  with (out/(mode+'.log')).open('x') as log:
   rc=subprocess.run(['docker','exec',cid,'/opt/dynamo/bin/entrypoint.sh','/opt/dynamo/bin/dev-editable.sh',mode],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=3600).returncode
  record['modes'][mode]=rc
  if rc:break
 record['passed']=record['modes']=={'python':0,'all':0}
finally:
 if cid:
  p=subprocess.run(['docker','rm','-f',cid],env=env,capture_output=True,text=True);record['cleanup']={'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 record['finished']=time.time();(out/'result.json').write_text(json.dumps(record,indent=2))
