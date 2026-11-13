#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,os,signal,subprocess,time,hashlib,tarfile
from pathlib import Path
root=Path('/data/dynamo-huawei-backup-20261112')
end=time.monotonic()+900
while time.monotonic()<end:
 d=json.loads((root/'cleanup-r2/status.json').read_text())
 if d['status']=='failed':raise RuntimeError(d.get('error'))
 if d['status']=='completed':break
 time.sleep(10)
else:raise TimeoutError('Cleanup incomplete')
post=root/'final-state';post.mkdir(exist_ok=False)
# The dedicated v29 daemon belongs only to this adaptation build environment.
pid=2076464;proc=Path('/proc')/str(pid)
if proc.exists():
 cmd=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode()
 assert cmd.strip()=='/data/dynamo-m10/docker-bin/dockerd --config-file /data/dynamo-m10/ascend-build-daemon.json'
 env=os.environ.copy();env['DOCKER_HOST']='unix:///run/dynamo-m10-docker.sock'
 running=subprocess.check_output(['/data/dynamo-m10/docker-bin/docker','ps','-q'],env=env,text=True)
 assert not running.strip()
 (post/'stopped-build-daemon.json').write_text(json.dumps({'pid':pid,'command':cmd,'active_container_ids':running.split(),'config_preserved':'/data/dynamo-m10/ascend-build-daemon.json','images_preserved':True},indent=2)+'\n')
 os.kill(pid,signal.SIGTERM)
 deadline=time.monotonic()+60
 while proc.exists() and time.monotonic()<deadline:time.sleep(1)
 assert not proc.exists(),'Dedicated daemon did not stop'
for cmd,name in [(['kubectl','get','pods','-A','-o','json'],'pods.json'),(['docker','ps','-a','--format','{{.ID}} {{.Names}} {{.Status}}'],'default-docker.txt'),(['df','-h','/root','/data'],'disk.txt')]:
 q=subprocess.run(cmd,capture_output=True,text=True,check=True,timeout=45);(post/name).write_text(q.stdout)
pods=json.loads((post/'pods.json').read_text())['items'];assert not [p for p in pods if p['metadata']['namespace'] in ['heyi-upgrade162','grove-system','kai-scheduler']]
(root/'FINAL-STATUS.json').write_text(json.dumps({'status':'completed','completed_at':time.time(),'scope':'Huawei 52 and 7.5 owned adaptation resources only','backup_verified':True,'shared_models_and_images_preserved':True,'build_daemon_stopped':True,'owned_pods_remaining':0,'stable':'1.5.1 / Ascend 0.23.0 r3','candidate':'1.6.2 / Ascend 0.30.0 2a239c2b50, not release-qualified'},indent=2)+'\n')
archive=root/'cleanup-records.tar.gz'
with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
 for name in ['cleanup','cleanup-r2','final-state','FINAL-STATUS.json','backup-status.json','cleanup-launch.log','cleanup-r2-launch.log','huawei55-cleanup.tar.gz','backup-huawei52.py','cleanup-huawei52.py','cleanup-huawei52-r2.py','finalize-huawei52.py']:
  tar.add(root/name,arcname=name)
h=hashlib.sha256(archive.read_bytes()).hexdigest();Path(str(archive)+'.sha256').write_text(h+'  '+archive.name+'\n')
print((root/'FINAL-STATUS.json').read_text(),flush=True)
