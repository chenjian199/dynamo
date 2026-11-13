#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Preserve Git history, tracked changes, workflow drafts and evidence before Huawei cleanup."""
import hashlib,json,subprocess,tarfile,time
from pathlib import Path
repo=Path('/home/metax/CJ_workspace/dynamo');out=repo.parent/'dynamo-huawei-backup-20261112';out.mkdir(exist_ok=True)
r=Path(__file__).resolve().parent
status={'started':time.time(),'status':'running','backup':str(out)}
def save():(r/'local-backup-status.json').write_text(json.dumps(status,indent=2)+'\n')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
save()
try:
 bundle=out/'dynamo-all-branches.bundle';assert not bundle.exists()
 subprocess.run(['git','bundle','create',str(bundle),'--all'],cwd=repo,check=True)
 v=subprocess.run(['git','bundle','verify',str(bundle)],cwd=repo,capture_output=True,text=True,check=True)
 (out/'git-bundle-verification.txt').write_text(v.stdout+v.stderr)
 repos={'heyi':repo,'upgrade-source':repo/'.worktrees/source-1.6.2-ascend-20261111','upgrade-ascend':repo/'.worktrees/source-1.6.2-ascend-20261111/.worktrees/dynamo-1.6.2-ascend-20261111'}
 manifest={};archive=out/'source-workspaces-and-evidence.tar.gz';assert not archive.exists()
 with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
  for key,root in repos.items():
   tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
   untracked=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z','adapter'],cwd=root).decode().split('\0')
   paths={root/name for name in tracked+untracked if name}
   # Preserve ignored field evidence too, excluding incomplete image copies and caches.
   for directory in ['adapter/results','adapter/upgrades']:
    if (root/directory).exists():paths.update(p for p in (root/directory).rglob('*') if p.is_file() or p.is_symlink())
   files={};excluded=[]
   for p in sorted(paths):
    name=str(p.relative_to(root))
    if not p.exists() and not p.is_symlink():continue
    if any(x in p.parts for x in ['__pycache__','.pytest_cache','.venv']) or name.endswith('.partial') or p.name in ['images.tar.gz']:
     excluded.append(name);continue
    assert not any(x in p.parts for x in ['.ssh','.kube']),name
    tar.add(p,arcname=key+'/'+name,recursive=False)
    if p.is_symlink():files[name]={'symlink':str(p.readlink())}
    else:files[name]={'sha256':sha(p),'size':p.stat().st_size}
   manifest[key]={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'files':files,'excluded_reproducible_or_partial':excluded}
   for kind,args in [('working',['diff','--binary','HEAD']),('index',['diff','--cached','--binary'])]:
    (out/f'{key}-{kind}.patch').write_bytes(subprocess.check_output(['git',*args],cwd=root))
   (out/f'{key}-status.txt').write_bytes(subprocess.check_output(['git','status','--porcelain=v1','--untracked-files=all'],cwd=root))
 (out/'workspace-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 # Validate every archived regular file against independently computed input hashes.
 checked=0
 with tarfile.open(archive,'r:gz') as tar:
  for member in tar:
   key,name=member.name.split('/',1);record=manifest[key]['files'][name]
   if member.isfile():
    h=hashlib.sha256();f=tar.extractfile(member)
    for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    assert h.hexdigest()==record['sha256'],member.name;checked+=1
 hashes={p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name!='SHA256SUMS'}
 (out/'SHA256SUMS').write_text(''.join(f'{s}  {n}\n' for n,s in hashes.items()))
 status.update(status='verified',checked_archive_files=checked,files={p.name:p.stat().st_size for p in out.iterdir() if p.is_file()})
except Exception as exc:
 status.update(status='failed',error=repr(exc));raise
finally:status['finished']=time.time();save();print(json.dumps(status,indent=2),flush=True)
