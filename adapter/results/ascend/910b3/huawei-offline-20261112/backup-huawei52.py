#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Archive verified stable delivery and explicitly unqualified upgrade candidate on Huawei 52."""
from pathlib import Path
import hashlib,json,os,subprocess,tarfile,time
root=Path('/data/dynamo-huawei-backup-20261112');p=root/'payload';p.mkdir(parents=True,exist_ok=True)
status={'status':'running','started':time.time()}
def save():(root/'backup-status.json').write_text(json.dumps(status,indent=2)+'\n')
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''):h.update(b)
 return h.hexdigest()
def run(cmd,path,env=None):
 r=subprocess.run(cmd,capture_output=True,env=env,timeout=600)
 path.write_bytes(r.stdout);Path(str(path)+'.stderr').write_bytes(r.stderr)
 if r.returncode:raise RuntimeError((cmd,r.returncode,r.stderr.decode(errors='replace')[-1000:]))
 return r.stdout
save()
try:
 stable=Path('/root/CJ_workspace/dynamo-m10/1.5.1-heyi-m10-4b1efe773c-r3/dynamo-ascend-1.5.1-heyi-m10-4b1efe773c-r3-arm64.tar')
 candidate=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/images-2a239c2b50/export/images.tar.gz')
 assert sha(stable)=='5b663b3cc2182bbd0333b712e88838fcddef6ac31e733fe1b42442bcade47ed4'
 assert sha(candidate)=='a7f7809603a495a6c95e3e33bcada14f9e09396c644f08b0c2c986a33e25e034'
 status['original_archives_verified']=True;save()
 c=p/'cluster';c.mkdir(exist_ok=True)
 env=os.environ.copy();env['KUBECONFIG']='/etc/rancher/k3s/k3s.yaml'
 helm='/root/CJ_workspace/m08-1029/linux-arm64/helm'
 releases=json.loads(run([helm,'list','-A','-o','json'],c/'helm-releases.json',env))
 for item in releases:
  if item['name'] not in ['heyi-upgrade162','heyi-grove','heyi-kai']:continue
  run([helm,'get','values',item['name'],'-n',item['namespace'],'-a','-o','json'],c/(item['name']+'-values.json'),env)
  run([helm,'get','metadata',item['name'],'-n',item['namespace'],'-o','json'],c/(item['name']+'-metadata.json'),env)
 for kind in ['nodes','pods','deployments','daemonsets','services','dgd','dgdr','podcliquesets.grove.io','queues.scheduling.run.ai','podgroups.scheduling.run.ai']:
  run(['kubectl','get',kind,'-A','-o','json'],c/(kind+'.json'))
 # No Secrets, kubeconfigs, private keys or registry credentials are backed up.
 d=p/'containers';d.mkdir(exist_ok=True)
 de=os.environ.copy();de['DOCKER_HOST']='unix:///run/dynamo-m10-docker.sock';docker='/data/dynamo-m10/docker-bin/docker'
 rows=subprocess.check_output([docker,'ps','-a','--format','{{.ID}} {{.Names}}'],env=de,text=True).splitlines()
 selected=[]
 for row in rows:
  cid,name=row.split()
  if not name.startswith(('heyi-upgrade','dynamo-m10-','dynamo-m02-build-ascend','m10-hybrid-')):continue
  item=json.loads(subprocess.check_output([docker,'inspect',cid],env=de))[0]
  # Environment variables can contain access credentials; retain only resource identities/mounts.
  item['Config'].pop('Env',None)
  (d/(name+'.json')).write_text(json.dumps(item,indent=2)+'\n')
  q=subprocess.run([docker,'logs','--timestamps',cid],env=de,capture_output=True,timeout=45)
  (d/(name+'.log')).write_bytes(q.stdout+q.stderr)
  run([docker,'diff',cid],d/(name+'.diff'),de)
  selected.append({'id':item['Id'],'name':name,'image':item['Image']})
 (d/'owned-containers.json').write_text(json.dumps(selected,indent=2)+'\n')
 e=p/'evidence';e.mkdir(exist_ok=True)
 run(['tar','-czf',str(e/'huawei52-upgrade-evidence.tar.gz'),'--exclude=images.tar.gz','--exclude=*.partial','-C','/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111','adapter/results'],e/'evidence-export.log')
 b=p/'candidate';b.mkdir(exist_ok=True)
 run(['tar','-czf',str(b/'build-artifacts.tar.gz'),'-C','/data/dynamo-upgrade-1.6.2-20261111/candidate-2a239c2b50','artifacts'],b/'build-export.log')
 run(['tar','-czf',str(b/'source-2a239c2b50.tar.gz'),'--exclude=.git','-C','/data/dynamo-upgrade-1.6.2-20261111','source-2a239c2b50'],b/'source-export.log')
 for src,dst in [(stable,p/'stable'/'dynamo-ascend-1.5.1-r3-arm64.tar'),(candidate,b/'images-2a239c2b50.tar.gz')]:
  dst.parent.mkdir(exist_ok=True);dst.symlink_to(src)
 (p/'README.md').write_text('''# 华为 Dynamo 下线备份（2026-11-12）

- stable/dynamo-ascend-1.5.1-r3-arm64.tar：已完成阶段验收的 1.5.1 / vLLM-Ascend 0.23.0，保留包内 validation.json 的明确限制。解压后的 import.sh 可导入 K3s/containerd/Docker。
- candidate/images-2a239c2b50.tar.gz：Dynamo 1.6.2 / vLLM-Ascend 0.30.0 的 runtime/dev/tools/operator OCI 镜像；候选源码 2a239c2b500ea7e48c86b7216b9878158db4802a。未完成正式验收，不可视为已发布版本。
- candidate/source-2a239c2b50.tar.gz、build-artifacts.tar.gz：候选源码和编译 wheel/构建产物。
- evidence/：52 与 7.5 的原始运行证据；失败和中断保留。当前冻结基础验收 7 项已通过；M03-F01 远端执行成功但证据回传超时，需要重试及后续门禁。
- cluster/：卸载前的 Helm values、集群对象和资源归属。不含 Secret、kubeconfig、私钥。不可对这些现场快照直接整目录 kubectl apply。
- containers/：适配容器身份、挂载、日志、可写层差异；容器可写层本身及编译缓存不归档。

## 校验及恢复

外层先执行 `sha256sum -c dynamo-huawei-offline-20261112.tar.sha256`，再 `tar -xf dynamo-huawei-offline-20261112.tar`，进入目录执行 `sha256sum -c SHA256SUMS`。
正式可复用包：`mkdir stable-unpacked && tar -xf stable/dynamo-ascend-1.5.1-r3-arm64.tar -C stable-unpacked`；按解压后的 README 和 import.sh 操作。不能将 1.6.2 候选镜像混入 1.5.1 清单。
候选镜像：K3s 使用 `gzip -dc candidate/images-2a239c2b50.tar.gz | k3s ctr -n k8s.io images import -`。导入不是验收通过；恢复升级工作需要另取沐曦主机同日 Git/工作区备份，读取中断记录并完成门禁。

模型权重、宿主机驱动、CANN 外部主机组件、Kubernetes、厂商 Device Plugin、共享监控/存储、凭据不在备份内。原有模型和系统组件保持原状。
''')
 manifest={str(f.relative_to(p)):sha(f) for f in p.rglob('*') if f.is_file()}
 (p/'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n,h in sorted(manifest.items())))
 archive=root/'dynamo-huawei-offline-20261112.tar';assert not archive.exists()
 status['status']='packing';save()
 with tarfile.open(archive,'w',dereference=True) as tar:tar.add(p,arcname='dynamo-huawei-offline-20261112')
 checked=0
 with tarfile.open(archive,'r:') as tar:
  for item in tar:
   if not item.isfile():continue
   rel=item.name.split('/',1)[1]
   if rel=='SHA256SUMS':continue
   h=hashlib.sha256();f=tar.extractfile(item)
   for data in iter(lambda:f.read(16*1024*1024),b''):h.update(data)
   assert h.hexdigest()==manifest[rel],rel;checked+=1
 outersha=sha(archive);Path(str(archive)+'.sha256').write_text(outersha+'  '+archive.name+'\n')
 link=Path('/root/CJ_workspace/dynamo-huawei-backup-20261112');assert not link.exists();link.symlink_to(root)
 status.update(status='verified',archive=str(archive),bytes=archive.stat().st_size,sha256=outersha,files_verified=checked,owned_containers=selected)
except Exception as exc:
 status.update(status='failed',error=repr(exc));raise
finally:status['finished']=time.time();save();print(json.dumps(status,indent=2),flush=True)
