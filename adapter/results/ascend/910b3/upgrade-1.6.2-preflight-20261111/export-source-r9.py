# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Freeze selected tracked product files; raw result trees are not build inputs."""
import hashlib,io,json,subprocess,tarfile
from pathlib import Path
source=Path('/home/metax/CJ_workspace/dynamo/.worktrees/source-1.6.2-ascend-20261111/.worktrees/dynamo-1.6.2-ascend-20261111');cache=Path('/var/tmp/dynamo-upgrade-162-build-cache')
assert not subprocess.check_output(['git','status','--porcelain'],cwd=source)
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
files=subprocess.check_output(['git','ls-files','-z'],cwd=source).decode().split('\0')
files=sorted(p for p in files if p and not p.startswith(('adapter/results/','reference/')) and (source/p).is_file())
manifest={'commit':commit,'files':{p:hashlib.sha256((source/p).read_bytes()).hexdigest() for p in files}}
with tarfile.open(cache/'source-49ef-delta.tar.gz') as t:old=json.load(t.extractfile('SOURCE.json'))
removed=set(old['files'])-set(manifest['files']);assert not removed,removed
out=cache/'source-1aabe-delta.tar.gz';assert not out.exists()
with tarfile.open(out,'w:gz') as t:
 for p,h in manifest['files'].items():
  if old['files'].get(p)!=h:t.add(source/p,arcname=p,recursive=False)
 data=(json.dumps(manifest,indent=2)+'\n').encode();entry=tarfile.TarInfo('SOURCE.json');entry.size=len(data);t.addfile(entry,io.BytesIO(data))
receipt={'commit':commit,'files':len(files),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'bytes':out.stat().st_size}
(cache/'source-1aabe-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
