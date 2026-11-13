#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect and normalize independently observed paired engine evidence for the standard verifier."""
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
    assert binding['nodes']==['huawei-52']
    model=next(m for m in env['environment']['nodes'][0]['models'] if m['id']==binding['models'][0])
    registry=env['environment']['image_inventory'][binding['image']]
    spec={'entrypoint':str(a.entrypoint.resolve().relative_to(Path.cwd())),
          'entrypoint_sha256':sha(a.entrypoint),'image_id':registry['config_id'],
          'image_reference':binding['image'],'source':env['environment']['image_source_root'],
          'output':remote,'container_name':name,'model_path':model['path'],
          'models':[model],
          'devices':list(range(binding['topology']['tp']*binding['topology'].get('workers',1))),
          'topology':binding['topology'],'case':a.case,'engine_args':[],
          'namespace':'close-'+env['identity'][:8]+'-'+a.case.lower()}
    specfile.write_text(json.dumps(spec,indent=2)+'\n')
    controller=root/'cache-case-remote.py'
    remote_parent=str(Path(remote).parent)
    remote_run(['mkdir','-p',remote_parent],check=True,timeout=30)
    subprocess.run(scp+[str(controller),str(root/'cache-case-probe.py'),str(root/'cache-routing-probe.py'),str(root/'cache-routing-client.py'),str(specfile),'huawei-52:'+remote_parent+'/'],check=True,timeout=60)
    result=remote_run(['python3',remote_parent+'/cache-case-remote.py',remote_parent+'/'+specfile.name],timeout=6500)
    raw.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(scp+['-r','huawei-52:'+remote,str(raw)],check=True,timeout=180)
    # Entry invocation is real and recorded by the remote Docker exec receipt.
    record=read_json(raw/'receipt.json')
    assert any(('/controller/cache-case-probe.py' in cmd or '/controller/cache-routing-probe.py' in cmd) for cmd in record['commands'])
    assert all(row['entrypoint_sha256']==spec['entrypoint_sha256'] for row in read_json(raw/'entry-invocations.json'))
    assert record['spec']==spec
    for path,expected in record['files'].items():
        assert sha(raw/path)==expected,path
    (attempt/(a.case+'-controller-hashes.json')).write_text(json.dumps({str(f):sha(f) for f in (Path(__file__),controller)},indent=2)+'\n')
    raise SystemExit(result.returncode)

if a.phase=='observe':
    normalizer=root/('normalize-cache-routing.py' if a.case=='M06-F01-C03' else 'normalize-cache-case.py')
    subprocess.run([sys.executable,str(normalizer),'--attempt',str(attempt),'--case',a.case],check=True)

if a.phase=='cleanup':
    # Only observe absence; remote runner already removed the exact owned ID.
    r=remote_run(['env','DOCKER_HOST=unix:///run/dynamo-m10-docker.sock','/data/dynamo-m10/docker-bin/docker','ps','-aq','--filter','name=^/'+name+'$'],capture_output=True,text=True,check=True,timeout=45)
    proof={'observed_remaining_ids':r.stdout.split(),'container_name':name,'stderr':r.stderr}
    (attempt/(a.case+'-cleanup-observation.json')).write_text(json.dumps(proof,indent=2)+'\n')
    assert not proof['observed_remaining_ids'],proof
    print(json.dumps(proof))
