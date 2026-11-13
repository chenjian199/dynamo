#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Remove known transfer cache on 7.5, retaining all models and imported images."""
import json,subprocess,time
from pathlib import Path
p=Path('/root/CJ_workspace/dynamo-huawei-cleanup-20261112');p.mkdir(exist_ok=False)
proof=json.loads(Path('/tmp/dynamo-huawei52-backup-verified.json').read_text());assert proof['status']=='verified' and proof['sha256']
status={'started':time.time(),'status':'running','archive_on_52':proof['archive'],'archive_sha256':proof['sha256'],'removed':[],'preserved':['model weights','all imported images','source and evidence','unrelated containers']}
def save():(p/'status.json').write_text(json.dumps(status,indent=2)+'\n')
save()
try:
 before=subprocess.check_output(['k3s','ctr','-n','k8s.io','images','ls','-q'],text=True);(p/'images-before.txt').write_text(before)
 rows=subprocess.check_output(['docker','ps','-a','--format','{{.ID}} {{.Names}}'],text=True);(p/'containers.txt').write_text(rows)
 assert not any(x.split()[1].startswith(('heyi-upgrade','dynamo-m10-')) for x in rows.splitlines()),'Unexpected owned Docker containers need separate inventory'
 base=Path('/data/dynamo-upgrade-1.6.2-20261111/distribution')
 listing=[{'name':x.name,'bytes':x.stat().st_size,'symlink':x.is_symlink()} for x in base.iterdir() if x.is_file()];(p/'transfer-cache-before.json').write_text(json.dumps(listing,indent=2)+'\n')
 for name in ['runtime-operator-5cbe.tar','runtime-1aabe.tar','runtime-49ef.tar','ascend-0.30-native.tar','old-h-bd801.tar','closure-2a239c2b50-images.tar.gz','closure-64815280cf-images.tar.gz']:
  f=base/name
  if f.exists():
   assert f.is_file() and not f.is_symlink() and f.resolve().parent==base
   status['removed'].append({'path':str(f),'bytes':f.stat().st_size});f.unlink();save()
 after=subprocess.check_output(['k3s','ctr','-n','k8s.io','images','ls','-q'],text=True);(p/'images-after.txt').write_text(after)
 assert set(before.splitlines())<=set(after.splitlines()),'Unexpected image removal'
 (p/'npu-after.txt').write_bytes(subprocess.check_output(['npu-smi','info']))
 (p/'disk-after.txt').write_bytes(subprocess.check_output(['df','-h','/data']))
 status.update(status='completed',freed_transfer_cache_bytes=sum(x['bytes'] for x in status['removed']))
except Exception as exc:status.update(status='failed',error=repr(exc));raise
finally:status['finished']=time.time();save();print(json.dumps(status,indent=2),flush=True)
