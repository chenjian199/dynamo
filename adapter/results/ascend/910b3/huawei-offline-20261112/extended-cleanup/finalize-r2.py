#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify preserved release bytes and seal a supplement after the extended cleanup."""
from pathlib import Path
import hashlib,json,subprocess,tarfile,time,shutil
root=Path('/data/dynamo-huawei-backup-20261112');work=root/'extended-cleanup-r2'
end=time.monotonic()+2400
while time.monotonic()<end:
 state=json.loads((work/'status.json').read_text())
 if state['status']=='failed':raise RuntimeError(state['error'])
 if state['status']=='completed':break
 time.sleep(10)
else:raise TimeoutError('Extended cleanup not complete')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''):h.update(b)
 return h.hexdigest()
assert sha(root/'dynamo-huawei-offline-20261112.tar')=='40697d9cfb95ba5896b5c7a139ba6c11f6efa222f85abd880c26894513390186'
assert sha(root/'huawei55-residual-source-and-evidence.tar.gz')=='9e6ee179fd9a22ed96d8a2fc3f469b592cf350b241222f4b4bff80c58a4b0766'
assert sha(work/'residual-source-and-evidence.tar.gz')==state['residual_backup']['sha256']
# Remove only the old packaging staging tree. The self-contained tar was reverified above.
payload=root/'payload'
if payload.exists():assert not payload.is_symlink() and payload.parent==root;shutil.rmtree(payload)
refs=subprocess.check_output(['k3s','ctr','-n','k8s.io','images','ls','-q'],text=True)
owned=[r for r in refs.splitlines() if '/heyi/dynamo' in r or r.startswith(('ghcr.io/ai-dynamo/grove/','ghcr.io/kai-scheduler/kai-scheduler/'))]
assert not owned,owned
verification={'status':'completed','host':'huawei-52','original_offline_archive_sha256':'40697d9cfb95ba5896b5c7a139ba6c11f6efa222f85abd880c26894513390186','original_archive_unchanged':True,'remaining_owned_containerd_image_refs':owned,'preserved_model_directory':'/data/dynamo-upgrade-1.6.2-20261111/models','packaging_staging_removed':True,'completed_at':time.time()}
(work/'final-verification.json').write_text(json.dumps(verification,indent=2)+'\n')
archive=root/'extended-cleanup-20261112.tar';assert not archive.exists()
with tarfile.open(archive,'w') as t:
 t.add(work,arcname='huawei52')
 for name in ['huawei55-residual-source-and-evidence.tar.gz','huawei55-extended-records.tar.gz','huawei-52-plan.json','clean-data.py','clean-data-r2.py','finalize-extended-r2.py','extended-cleanup-interruption.json']:
  t.add(root/name,arcname=name)
Path(str(archive)+'.sha256').write_text(sha(archive)+'  '+archive.name+'\n')
print(json.dumps({'status':'completed','supplement':str(archive),'bytes':archive.stat().st_size,'verification':verification},indent=2),flush=True)
