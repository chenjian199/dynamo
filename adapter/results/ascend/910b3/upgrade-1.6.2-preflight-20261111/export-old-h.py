# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import pathlib,json,subprocess,os,time,hashlib
r=pathlib.Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'distribution';out=w/'old-h-bd801.tar';assert not out.exists()
record=json.loads((r/'baseline-h/runtime-r2.json').read_text());image=record['image'];env={**os.environ,'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH']}
assert json.loads(subprocess.check_output(['docker','image','inspect',image['tag']],env=env))[0]['Id']==image['id']
a=['docker','save','-o',str(out),image['tag']];record.update(argv=a,started=time.time())
(w/'old-h-bd801-start.json').write_text(json.dumps(record,indent=2))
with (w/'old-h-bd801-export.log').open('x') as f:subprocess.run(a,env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=1200)
h=hashlib.sha256()
with out.open('rb') as f:
 for b in iter(lambda:f.read(16*1024*1024),b''):h.update(b)
(w/'old-h-bd801.sha256').write_text(h.hexdigest()+'  '+out.name+'\n');record.update(sha256=h.hexdigest(),bytes=out.stat().st_size,finished=time.time());(w/'old-h-bd801.json').write_text(json.dumps(record,indent=2))
with (w/'old-h-bd801-import-52.log').open('x') as f:subprocess.run(['k3s','ctr','-n','k8s.io','images','import','--platform','linux/arm64',str(out)],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=900)
(w/'old-h-bd801-import-52.json').write_text(json.dumps({'passed':True,'image':image,'sha256':h.hexdigest(),'finished':time.time()},indent=2))
