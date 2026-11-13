#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Remove identified Huawei adaptation resources only after archive verification."""
from pathlib import Path
import hashlib,json,os,signal,subprocess,time
root=Path('/data/dynamo-huawei-backup-20261112');out=root/'cleanup-r2';out.mkdir(exist_ok=False)
status={'started':time.time(),'status':'running','actions':[],'preserved':['all images','all model weights','shared cluster components','unknown containers','source and evidence directories not fully covered by this cleanup']}
def save():(out/'status.json').write_text(json.dumps(status,indent=2)+'\n')
def call(cmd,env=None,check=True,timeout=180):
 p=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=timeout)
 status['actions'].append({'argv':cmd,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr});save()
 if check and p.returncode:raise RuntimeError((cmd,p.returncode,p.stderr[-1000:]))
 return p
save()
try:
 backup=json.loads((root/'backup-status.json').read_text());assert backup['status']=='verified'
 assert Path(backup['archive']).stat().st_size==backup['bytes']
 kenv=os.environ.copy();kenv['KUBECONFIG']='/etc/rancher/k3s/k3s.yaml';helm='/root/CJ_workspace/m08-1029/linux-arm64/helm'
 pods=json.loads(call(['kubectl','get','pods','-A','-o','json']).stdout)
 assert not [x for x in pods['items'] if 'kai' in x['spec'].get('schedulerName','')], 'KAI workloads remain; keep shared scheduler'
 for kind in ['dgd','dgdr','podcliquesets.grove.io','podgroups.scheduling.run.ai']:
  assert not json.loads(call(['kubectl','get',kind,'-A','-o','json']).stdout)['items'],kind
 # The three exact releases were created by this adaptation session and backed up.
 for name,ns in [('heyi-upgrade162','heyi-upgrade162'),('heyi-grove','grove-system'),('heyi-kai','kai-scheduler')]:
  active=json.loads(call([helm,'list','-n',ns,'-o','json'],kenv).stdout)
  if not any(x['name']==name for x in active):
   status['actions'].append({'already_uninstalled_release':name,'namespace':ns});save();continue
  saved=json.loads((root/'payload/cluster'/(name+'-metadata.json')).read_text())
  now=json.loads(call([helm,'get','metadata',name,'-n',ns,'-o','json'],kenv).stdout)
  assert saved==now,(name,'release changed after backup')
  call([helm,'uninstall',name,'-n',ns,'--wait','--timeout','180s'],kenv,timeout=210)
 for socket,label in [('unix:///run/dynamo-m10-docker.sock','m10'),('unix:///var/run/docker.sock','default')]:
  env=os.environ.copy();env['DOCKER_HOST']=socket;docker='/data/dynamo-m10/docker-bin/docker' if label=='m10' else '/usr/bin/docker'
  rows=call([docker,'ps','-a','--format','{{.ID}} {{.Names}}'],env).stdout.splitlines()
  for row in rows:
   cid,name=row.split()
   if not name.startswith(('heyi-upgrade','dynamo-m10-','dynamo-m02-build-ascend','m10-hybrid-')):continue
   item=json.loads(subprocess.check_output([docker,'inspect',cid],env=env,timeout=30))[0]
   item['Config'].pop('Env',None);(out/(label+'-'+name+'.json')).write_text(json.dumps(item,indent=2)+'\n')
   logs=subprocess.run([docker,'logs','--tail','2000',cid],env=env,capture_output=True,timeout=30)
   (out/(label+'-'+name+'.log')).write_bytes(logs.stdout+logs.stderr)
   # Full immutable ID; never remove anonymous or unrelated containers.
   call([docker,'stop','--time','20',item['Id']],env,timeout=40)
   call([docker,'rm',item['Id']],env,timeout=45)
  remaining=call([docker,'ps','-a','--format','{{.ID}} {{.Names}}'],env).stdout.splitlines()
  assert not [x for x in remaining if x.split()[1].startswith(('heyi-upgrade','dynamo-m10-','dynamo-m02-build-ascend','m10-hybrid-'))]
 for pid,port in [(151647,'18962'),(515930,'18963')]:
  proc=Path('/proc')/str(pid)
  if not proc.exists():continue
  cmd=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode()
  assert 'python3 -m http.server '+port in cmd and '/data/dynamo-upgrade-1.6.2-20261111/distribution/closure-' in cmd
  os.kill(pid,signal.SIGTERM);status['actions'].append({'stopped_http_pid':pid,'command':cmd});save()
 # Delete only disposable compiler and per-test build directories; preserve source/results/models.
 import shutil
 base=Path('/data/dynamo-upgrade-1.6.2-20261111')
 for name in ['cache','editable-cases','wheel-cases']:
  target=base/name
  if target.exists():
   assert target.resolve().parent==base and not target.is_symlink()
   subprocess.run(['du','-sb',str(target)],stdout=(out/(name+'-size-before.txt')).open('w'),check=True)
   shutil.rmtree(target);status['actions'].append({'removed_build_cache':str(target)});save()
 # Intermediate image export copies are disposable; imported image stores remain intact.
 distribution=base/'distribution'
 exports=['tools-1aabe.tar','tools-49ef.tar','runtime-operator-5cbe.tar','runtime-1aabe.tar','runtime-49ef.tar','ascend-0.30-native.tar','old-h-bd801.tar']
 for name in exports:
  target=distribution/name
  if target.is_file() and not target.is_symlink():
   status['actions'].append({'removed_intermediate_image_export':str(target),'bytes':target.stat().st_size,'reason':'Historical transfer cache; stable r3 and latest 2a239c2b50 archives verified; image stores retained'})
   target.unlink();save()
 for cmd,name in [(['kubectl','get','pods','-A','-o','json'],'pods-after.json'),([helm,'list','-A','-o','json'],'helm-after.json'),(['df','-h','/root','/data'],'disk-after.txt'),(['npu-smi','info'],'npu-after.txt')]:
  result=call(cmd,kenv,timeout=60);(out/name).write_text(result.stdout)
 status['status']='completed'
except Exception as exc:status.update(status='failed',error=repr(exc));raise
finally:status['finished']=time.time();save();print(json.dumps({k:v for k,v in status.items() if k!='actions'},indent=2),flush=True)
