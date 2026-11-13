# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import hashlib,json,time,urllib.request,subprocess
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111/distribution');r.mkdir(parents=True,exist_ok=True);url='http://192.168.7.7:18962/';deadline=time.monotonic()+5400
while True:
 try:
  with urllib.request.urlopen(url+'runtime-1aabe.sha256',timeout=10) as f:check=f.read().decode()
  break
 except Exception:
  if time.monotonic()>deadline:raise TimeoutError('Native base export not ready')
  time.sleep(10)
(r/'runtime-1aabe.sha256').write_text(check);p=r/'runtime-1aabe.tar';a=['curl','--fail','--retry','3','--max-time','900','-o',str(p)+'.tmp',url+p.name]
with (r/'runtime-1aabe-import.log').open('x') as log:
 subprocess.run(a,stdout=log,stderr=subprocess.STDOUT,check=True);Path(str(p)+'.tmp').rename(p)
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(16*1024*1024),b''):h.update(block)
 assert h.hexdigest()==check.split()[0]
 subprocess.run(['k3s','ctr','-n','k8s.io','images','import',str(p)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
(r/'runtime-1aabe-import.json').write_text(json.dumps({'sha256':h.hexdigest(),'bytes':p.stat().st_size,'passed':True,'finished':time.time()},indent=2))
