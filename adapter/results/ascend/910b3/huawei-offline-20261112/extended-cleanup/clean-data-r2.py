#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Archive residual small artifacts, remove exact adaptation image refs and owned data roots."""
import hashlib,json,os,shutil,subprocess,sys,tarfile,time
from pathlib import Path
plan=json.loads(Path(sys.argv[1]).read_text());out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=False)
status={'host':plan['host'],'status':'running','started':time.time(),'actions':[],'skipped_images':[]}
def save():(out/'status.json').write_text(json.dumps(status,indent=2)+'\n')
def run(args,check=True,timeout=180):
 p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 row={'argv':args,'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr};status['actions'].append(row);save()
 if check and p.returncode:raise RuntimeError(row)
 return p.stdout
def sha(f):
 h=hashlib.sha256()
 with f.open('rb') as stream:
  for b in iter(lambda:stream.read(16*1024*1024),b''):h.update(b)
 return h.hexdigest()
def protected_path(p):return any(p==Path(x) or Path(x) in p.parents for x in plan['preserve_subtrees'])
def owned_image(ref):
 return '/heyi/dynamo' in '/'+ref or ref.startswith('heyi/dynamo') or ref.startswith(('ghcr.io/ai-dynamo/grove/','ghcr.io/kai-scheduler/kai-scheduler/'))
def canonical(ref):return ref.removeprefix('docker-pullable://').removeprefix('docker://')
save()
try:
 targets=[Path(x) for x in plan['paths']]
 assert all(p.parent in [Path('/data'),Path('/root/CJ_workspace')] and not p.is_symlink() and 'backup' not in p.name and 'cleanup' not in p.name for p in targets)
 if plan['host']=='huawei-52':
  assert sha(Path(plan['backup_path_on_52']))==plan['backup_archive_sha256']
 else:
  receipt=json.loads(Path(sys.argv[1]).with_name('huawei52-backup-verification.json').read_text());assert receipt['status']=='verified' and receipt['sha256']==plan['backup_archive_sha256']
 mounted=run(['findmnt','-rn','-o','TARGET']).splitlines()
 assert not [m for m in mounted if any(Path(m)==p or p in Path(m).parents for p in targets)], 'Owned paths contain active mount points'
 # Dedicated build store belongs to our stopped daemon. Preserve anonymous build identities before deleting it.
 store=Path('/data/dynamo-m10/docker-store/containers')
 if store.exists():
  store_meta=[]
  for f in store.glob('*/config.v2.json'):
   d=json.loads(f.read_text());assert not d['State']['Running']
   assert d.get('Args')==['-c','bash /opt/dynamo/bin/dev-setup.sh'],d.get('Name')
   store_meta.append({k:d.get(k) for k in ['ID','Name','Image','Path','Args','Created','State']})
  (out/'isolated-build-containers.json').write_text(json.dumps(store_meta,indent=2)+'\n')
  for p in Path('/proc').glob('[0-9]*/cmdline'):
   try:cmd=p.read_bytes()
   except (FileNotFoundError,PermissionError):continue
   assert not (b'/data/dynamo-m10/docker-bin/dockerd' in cmd and b'ascend-build-daemon.json' in cmd), 'Build daemon still active'
 # Resume the completed immutable archive using streaming verification. Hardlinks are
 # checked against the manifest of their earlier target instead of seeking backwards in gzip.
 prior=out.parent/'extended-cleanup'
 source=prior/'residual-source-and-evidence.tar.gz'
 meta=json.loads((prior/'residual-manifest.json').read_text());manifest=meta['included']
 checked={};links={}
 with tarfile.open(source,'r|gz') as tar:
  for member in tar:
   assert member.name in manifest,member.name
   if member.isreg():
    stream=tar.extractfile(member);h=hashlib.sha256()
    for data in iter(lambda:stream.read(8*1024*1024),b''):h.update(data)
    digest=h.hexdigest();assert digest==manifest[member.name]['sha256'],member.name
    checked[member.name]=digest
   elif member.islnk():links[member.name]=member.linkname
   else:raise AssertionError(('Unexpected archive member type',member.name,member.type))
 while links:
  resolved=[]
  for name,target in links.items():
   if target in checked:
    assert checked[target]==manifest[name]['sha256'],(name,target)
    checked[name]=checked[target];resolved.append(name)
  assert resolved,('Unresolved hardlink targets',links)
  for name in resolved:del links[name]
 assert set(checked)==set(manifest)
 snapshot=out/'residual-source-and-evidence.tar.gz';os.link(source,snapshot)
 shutil.copy2(prior/'residual-manifest.json',out/'residual-manifest.json')
 status['residual_backup']={'path':str(snapshot),'sha256':sha(snapshot),'files':len(manifest),'verification':'streamed regular bytes plus hardlink target SHA256 equivalence'}
 status['status']='removing_images';save()
 # Use CRI to remove owned tags and image-ID aliases together. Other applications' image refs are protected.
 images=json.loads(run(['k3s','crictl','images','-o','json']))['images']
 containers=json.loads(run(['k3s','crictl','ps','-a','-o','json']))['containers']
 in_use={canonical(c.get('imageRef','')) for c in containers}|{canonical(c.get('image',{}).get('image','')) for c in containers}
 protected={canonical(x) for x in plan['protected_images']}|in_use
 for im in images:
  refs=im.get('repoTags',[])+im.get('repoDigests',[])
  own=[x for x in refs if owned_image(x)]
  if not own:continue
  all_refs=set(refs)|{im['id']}
  if all_refs&protected:
   status['skipped_images'].append({'refs':own,'reason':'referenced by a workload or retained container'});save();continue
  foreign=[x for x in refs if not owned_image(x)]
  if foreign:
   for ref in own:run(['k3s','ctr','-n','k8s.io','images','rm',ref])
  else:run(['k3s','crictl','rmi',im['id']],timeout=300)
 # Imports not registered with CRI may still have named containerd image refs.
 for ref in run(['k3s','ctr','-n','k8s.io','images','ls','-q']).splitlines():
  if owned_image(ref) and canonical(ref) not in protected:run(['k3s','ctr','-n','k8s.io','images','rm',ref])
 docker_ids=run(['docker','ps','-aq']).splitlines();docker_used=set()
 if docker_ids:
  for c in json.loads(subprocess.check_output(['docker','inspect',*docker_ids],text=True)):
   docker_used.update([c['Image'],c['Config']['Image']])
 for line in run(['docker','images','--no-trunc','--format','{{json .}}']).splitlines():
  row=json.loads(line);ref=row['Repository']+':'+row['Tag']
  if not owned_image(ref):continue
  if row['ID'] in docker_used or ref in docker_used or ref in protected:
   status['skipped_images'].append({'refs':[ref],'reason':'Docker container or workload reference'});save();continue
  run(['docker','rmi',ref],timeout=300)
 status['status']='removing_data';save()
 # Refresh live hostPath users immediately before deleting. Workloads may have changed after inventory.
 # Global workload hostPaths are supplied from the control plane snapshot, and are conservative blockers.
 for target in targets:
  if not target.exists():continue
  active=[x for x in plan.get('protected_hostpaths',[]) if Path(x)==target or target in Path(x).parents]
  assert not active,('Active workload hostPath under target',str(target),active)
  if target.is_dir():
   preserves=[Path(x) for x in plan['preserve_subtrees'] if target in Path(x).parents]
   if preserves:
    assert all(x.parent==target for x in preserves)
    for child in list(target.iterdir()):
     if child in preserves:continue
     if child.is_dir() and not child.is_symlink():shutil.rmtree(child)
     else:child.unlink()
    status['actions'].append({'removed_directory_contents_except_models':str(target),'preserved':[str(x) for x in preserves if x.exists()]})
    if not any(target.iterdir()):target.rmdir()
   else:shutil.rmtree(target);status['actions'].append({'removed_directory':str(target)})
  else:target.unlink();status['actions'].append({'removed_file':str(target)})
  save()
 after=run(['k3s','ctr','-n','k8s.io','images','ls']);(out/'ctr-images-after.txt').write_text(after)
 (out/'docker-images-after.txt').write_text(run(['docker','images','--no-trunc']))
 (out/'disk-after.txt').write_text(run(['df','-B1','/data','/root']))
 remaining=[str(p) for p in targets if p.exists() and not any(p in Path(x).parents for x in plan['preserve_subtrees'])]
 assert not remaining,remaining
 status.update(status='completed',remaining_targets=remaining)
except Exception as exc:status.update(status='failed',error=repr(exc));raise
finally:status['finished']=time.time();save();print(json.dumps({k:v for k,v in status.items() if k!='actions'},indent=2),flush=True)
