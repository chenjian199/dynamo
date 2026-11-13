#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect full immutable image archive bytes for coordinator-side release verification."""
import hashlib,json,subprocess,time
from pathlib import Path
r=Path(__file__).resolve().parent;out=r/'image-bytes-2a239c2b50';out.mkdir(exist_ok=False)
config='/var/tmp/dynamo-regression-1028/ssh-config-upgrade-fresh'
remote='/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/images-2a239c2b50/export/'
record={'started':time.time(),'status':'waiting_for_export','primary_archive_host':'huawei-52','primary_archive':remote+'images.tar.gz'}
def save():(out/'collection.json').write_text(json.dumps(record,indent=2)+'\n')
save()
try:
 deadline=time.monotonic()+10800
 while True:
  x=subprocess.run(['ssh','-F',config,'huawei-52','cat '+remote+'image-export.json'],capture_output=True,text=True,timeout=30)
  if x.returncode==0:
   meta=json.loads(x.stdout);(out/'image-export.json').write_text(json.dumps(meta,indent=2)+'\n');break
  if time.monotonic()>deadline:raise TimeoutError('Export not ready')
  time.sleep(30)
 record['status']='copying_full_archive';record['copy_started']=time.time();save()
 archive=out/'images.tar.gz';partial=out/'images.tar.gz.partial'
 with (out/'scp.log').open('x') as log:subprocess.run(['scp','-O','-F',config,'huawei-52:'+remote+'images.tar.gz',str(partial)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=21600)
 h=hashlib.sha256()
 with partial.open('rb') as f:
  for data in iter(lambda:f.read(16*1024*1024),b''):h.update(data)
 assert h.hexdigest()==meta['sha256']
 partial.rename(archive);record.update(status='verified_complete_copy',bytes=archive.stat().st_size,sha256=h.hexdigest())
except Exception as e:record.update(status='failed',error=repr(e))
finally:
 record['finished']=time.time();save();print(json.dumps(record,indent=2),flush=True)
