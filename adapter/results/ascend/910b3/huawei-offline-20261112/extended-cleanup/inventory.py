#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,subprocess,os
from pathlib import Path

def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=90)
 return {'argv':args,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
records={}
for key,args in {
 'ctr_images':['k3s','ctr','-n','k8s.io','images','ls'],
 'cri_images':['k3s','crictl','images','-o','json'],
 'cri_containers':['k3s','crictl','ps','-a','-o','json'],
 'docker_images':['docker','images','--no-trunc','--format','{{json .}}'],
 'docker_containers':['docker','ps','-a','--no-trunc','--format','{{json .}}'],
 'mounts':['findmnt','-J'],
}.items():records[key]=run(args)
paths=[]
for base in [Path('/data'),Path('/root/CJ_workspace')]:
 for p in base.iterdir():
  if any(x in p.name.lower() for x in ['dynamo','m08','m09','m10','adapter-workflow','ascend-delivery','aiperf']):
   entry={'path':str(p),'symlink':p.is_symlink(),'resolved':str(p.resolve())}
   if p.is_dir() and not p.is_symlink():
    entry['top_level']=sorted(x.name for x in p.iterdir())
    entry['size']=run(['du','-sxB1',str(p)])
   else:entry['bytes']=p.lstat().st_size
   paths.append(entry)
records['paths']=paths
print(json.dumps(records,indent=2))
