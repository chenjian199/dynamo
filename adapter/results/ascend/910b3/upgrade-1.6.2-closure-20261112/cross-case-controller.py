#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect and normalize independently observed Operator evidence for the standard verifier."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

p=argparse.ArgumentParser()
p.add_argument('phase',choices=('run','observe','cleanup'))
p.add_argument('--attempt',type=Path,required=True)
p.add_argument('--case',required=True)
p.add_argument('--entrypoint',type=Path,required=True)
a=p.parse_args()
root=Path(__file__).resolve().parent
attempt=a.attempt.resolve(); env=json.loads((attempt/'envelope.json').read_text())
binding=env['environment']['case_bindings'][a.case]
ssh=['ssh','-F',env['environment']['ssh_config'],'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','huawei-52']
scp=['scp','-O','-F',env['environment']['ssh_config']]
raw=attempt/'raw'/a.case
remote='/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/'+attempt.parents[2].name+'/'+a.case+'/'+attempt.name
specfile=attempt/(a.case+'-remote-spec.json')
name='heyi-close-'+env['identity'][:8]+'-'+a.case.lower()+'-'+attempt.name

def remote_run(argv,**kw):
    return subprocess.run(ssh+[shlex.join(argv)],**kw)

def read_json(path):
    text=path.read_text()
    decoder=json.JSONDecoder()
    try:return json.loads(text)
    except json.JSONDecodeError:
        for offset,char in enumerate(text):
            if char!='{' or (offset and text[offset-1]!='\n'):continue
            try:
                value,used=decoder.raw_decode(text[offset:])
                if not text[offset+used:].strip():return value
            except json.JSONDecodeError:pass
        raise

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

if a.phase=='run':
    assert binding['nodes']==['huawei-52','huawei-55']
    model=next(m for m in env['environment']['nodes'][0]['models'] if m['id']=='Qwen3-0.6B')
    registry=env['environment']['image_inventory'][binding['image']]
    spec={'entrypoint':str(a.entrypoint.resolve().relative_to(Path.cwd())),
          'entrypoint_sha256':sha(a.entrypoint),'image_id':registry['config_id'],
          'image_reference':binding['image'],'source':env['environment']['image_source_root'],
          'output':remote,'name':name,'model_path':model['path'],
          'operator_image':'heyi/dynamo-operator:1.6.2-heyi-upgrade-2a239c2b50-arm64',
          'models':[model],
          'remote_model_path':next(m['path'] for n in env['environment']['nodes'] if n['id']=='huawei-55' for m in n['models'] if m['id']==model['id']),
          'devices':list(range(binding['topology'].get('tp',1)*binding['topology'].get('dp',1)*binding['topology'].get('pp',1))),
          'topology':binding['topology'],'kubernetes_nodes':binding['kubernetes_nodes'],'case':a.case,'engine_args':[],
          'namespace':'heyi-upgrade162'}
    remote_model=next(m for n in env['environment']['nodes'] if n['id']=='huawei-55' for m in n['models'] if m['id']==model['id'])
    cmd=['ssh','-F',env['environment']['ssh_config'],'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','huawei-55',shlex.join(['python3',str(Path(spec['source'])/'adapter/workflow/assets.py'),json.dumps(remote_model)])]
    with (attempt/(a.case+'-model-55.json')).open('x') as f:
        subprocess.run(cmd,stdout=f,check=True,timeout=1800)
    (attempt/(a.case+'-model-55-command.json')).write_text(json.dumps(cmd,indent=2)+'\n')
    specfile.write_text(json.dumps(spec,indent=2)+'\n')
    controller=root/'cross-case-remote.py'
    remote_parent=str(Path(remote).parent)
    remote_run(['mkdir','-p',remote_parent],check=True,timeout=30)
    subprocess.run(scp+[str(controller),str(specfile),'huawei-52:'+remote_parent+'/'],check=True,timeout=60)
    result=remote_run(['python3',remote_parent+'/cross-case-remote.py',remote_parent+'/'+specfile.name],timeout=6500)
    raw.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(scp+['-r','huawei-52:'+remote,str(raw)],check=True,timeout=180)
    # Entry invocation is real and recorded by the remote Docker exec receipt.
    record=read_json(raw/'receipt.json')
    assert any(str(Path(spec['source'])/spec['entrypoint']) in cmd for cmd in record['commands'])
    assert record['spec']==spec
    for path,expected in record['files'].items():
        assert sha(raw/path)==expected,path
    (attempt/(a.case+'-controller-hashes.json')).write_text(json.dumps({str(f):sha(f) for f in (Path(__file__),controller)},indent=2)+'\n')
    raise SystemExit(result.returncode)

if a.phase=='observe':
    normalizer=root/'normalize-cross-case.py'
    subprocess.run([sys.executable,str(normalizer),'--attempt',str(attempt),'--case',a.case],check=True)

if a.phase=='cleanup':
    dgd=remote_run(['kubectl','-n','heyi-upgrade162','get','dgd',name,'--ignore-not-found','-o','name'],capture_output=True,text=True,check=True,timeout=60)
    pods=remote_run(['kubectl','-n','heyi-upgrade162','get','pods','-o','json'],capture_output=True,text=True,check=True,timeout=60)
    left=[p['metadata']['uid'] for p in json.loads(pods.stdout)['items'] if p['metadata']['name'].startswith(name+'-')]
    proof={'remaining_dgd':dgd.stdout.strip(),'remaining_pod_uids':left,'name':name}
    (attempt/(a.case+'-cleanup-observation.json')).write_text(json.dumps(proof,indent=2)+'\n')
    assert not proof['remaining_dgd'] and not left,proof
    print(json.dumps(proof))
