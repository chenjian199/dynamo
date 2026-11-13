# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import hashlib,json,subprocess,time,urllib.request
from pathlib import Path
out=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/images-2a239c2b50-55')
out.mkdir(parents=True,exist_ok=False)
archive=Path('/data/dynamo-upgrade-1.6.2-20261111/distribution/closure-2a239c2b50-images.tar.gz')
assert not archive.exists();archive.parent.mkdir(parents=True,exist_ok=True)
url='http://192.168.7.7:18963/'
record={'started':time.time(),'status':'waiting_for_archive'}
(out/'status.json').write_text(json.dumps(record,indent=2)+'\n')
try:
 deadline=time.monotonic()+9000
 while True:
  try:
   with urllib.request.urlopen(url+'closure-2a239c2b50-images.json',timeout=10) as f:meta=json.load(f)
   break
  except Exception:
   if time.monotonic()>deadline:raise TimeoutError('Export descriptor unavailable')
   time.sleep(15)
 (out/'export-descriptor.json').write_text(json.dumps(meta,indent=2)+'\n')
 with (out/'download.log').open('x') as f:
  subprocess.run(['curl','--fail','--retry','3','--max-time','1800','-o',str(archive)+'.partial',url+meta['archive']],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=1900)
 h=hashlib.sha256()
 with Path(str(archive)+'.partial').open('rb') as f:
  for block in iter(lambda:f.read(16*1024*1024),b''):h.update(block)
 assert h.hexdigest()==meta['sha256']
 Path(str(archive)+'.partial').rename(archive)
 record.update(status='importing',archive_sha256=h.hexdigest(),archive_bytes=archive.stat().st_size)
 (out/'status.json').write_text(json.dumps(record,indent=2)+'\n')
 with (out/'import.log').open('x') as f:
  subprocess.run(['k3s','ctr','-n','k8s.io','images','import','--platform','linux/arm64',str(archive)],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=1800)
 inventory={}
 for tag,item in meta['inventory'].items():
  canonical=tag if tag.startswith('docker.io/') else 'docker.io/'+tag
  pinned=item['repo_digests'][0]
  rows=subprocess.check_output(['k3s','ctr','-n','k8s.io','images','ls'],text=True).splitlines()
  row=next(x.split() for x in rows if x.split()[0]==canonical)
  assert row[2]==pinned.split('@')[1],(row,pinned)
  subprocess.run(['k3s','ctr','-n','k8s.io','images','tag',canonical,pinned],check=True)
  cri=json.loads(subprocess.check_output(['k3s','crictl','inspecti',pinned]))
  assert cri['status']['id']==item['config_id'] and pinned in cri['status']['repoDigests']
  inventory[tag]=cri['status']
 (out/'inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
 record['status']='imported';record['inventory']=inventory
except Exception as e:
 record['status']='failed';record['error']=repr(e)
finally:
 record['finished']=time.time()
 (out/'status.json').write_text(json.dumps(record,indent=2)+'\n')
 print(json.dumps(record),flush=True)
