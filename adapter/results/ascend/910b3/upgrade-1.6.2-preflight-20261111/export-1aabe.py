# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Export an immutable image as soon as its sealed build record is available."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
kind=sys.argv[1];assert kind in ['runtime','tools'];r=Path('/data/dynamo-upgrade-1.6.2-20261111');w=r/'distribution';prefix=kind+'-1aabe';out=w/(prefix+'.tar');assert not out.exists();deadline=time.monotonic()+5400
while True:
 p=r/'candidate-1aabe/images.json';build=json.loads(p.read_text()) if p.exists() else {}
 if build.get(kind,{}).get('id'):break
 if build.get(kind,{}).get('exit_code',0):raise RuntimeError('Image build failed')
 if time.monotonic()>deadline:raise TimeoutError('Image build not ready')
 time.sleep(10)
assert build['source_commit']=='1aabe435ad56922cf7e12f92f64df2dc39531ccb';image=build[kind];env={**os.environ,'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH']};actual=json.loads(subprocess.check_output(['docker','image','inspect',image['tag']],env=env))[0];assert actual['Id']==image['id'];record={'started':time.time(),'source_commit':build['source_commit'],'image':image};(w/(prefix+'-start.json')).write_text(json.dumps(record,indent=2))
with (w/(prefix+'-export.log')).open('x') as log:subprocess.run(['docker','save','-o',str(out),image['tag']],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1500)
h=hashlib.sha256()
with out.open('rb') as f:
 for b in iter(lambda:f.read(16*1024*1024),b''):h.update(b)
(w/(prefix+'.sha256')).write_text(h.hexdigest()+'  '+out.name+'\n');record.update(sha256=h.hexdigest(),bytes=out.stat().st_size,finished=time.time());(w/(prefix+'.json')).write_text(json.dumps(record,indent=2))
with (w/(prefix+'-import-52.log')).open('x') as log:subprocess.run(['k3s','ctr','-n','k8s.io','images','import','--platform','linux/arm64',str(out)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
(w/(prefix+'-import-52.json')).write_text(json.dumps({'passed':True,'image':image,'sha256':h.hexdigest(),'finished':time.time()},indent=2))
