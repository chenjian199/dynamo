#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Copy and verify the existing candidate source for tests-only mounts on node 55."""
import hashlib,json,subprocess,tarfile,time
from pathlib import Path
root=Path('/data/dynamo-upgrade-1.6.2-20261111');dest=root/'source-2a239c2b50'
assert not dest.exists()
archive=root/'distribution/source-2a239c2b50.tar.gz'
subprocess.run(['curl','--fail','--silent','--show-error','--connect-timeout','10','--max-time','300','http://192.168.7.7:18963/source-2a239c2b50.tar.gz','-o',str(archive)],check=True)
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='bbfcce3775d03d322ab267c5e07581e68ab99d2acb359705cbc1432f8342c0e2'
dest.mkdir()
with tarfile.open(archive) as t:
 for item in t.getmembers():
  resolved=(dest/item.name).resolve();assert resolved.is_relative_to(dest)
  if item.issym():assert (resolved.parent/item.linkname).resolve().is_relative_to(dest)
  if item.islnk():assert (dest/item.linkname).resolve().is_relative_to(dest)
 t.extractall(dest)
manifest=json.loads((dest/'SOURCE.json').read_text());assert manifest['commit']=='2a239c2b500ea7e48c86b7216b9878158db4802a'
for name,checksum in manifest['files'].items():assert hashlib.sha256((dest/name).read_bytes()).hexdigest()==checksum,name
out=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112');out.mkdir(parents=True,exist_ok=True)
receipt={'source_commit':manifest['commit'],'source_root':str(dest),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'verified_files':len(manifest['files']),'finished':time.time()}
(out/'source-55-2a239c2b50.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt),flush=True)
