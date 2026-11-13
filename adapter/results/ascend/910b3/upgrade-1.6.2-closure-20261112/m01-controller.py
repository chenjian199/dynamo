#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect and normalize independently observed M01 evidence for the standard verifier."""
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
    model=next(m for m in env['environment']['nodes'][0]['models'] if m['id']=='Qwen3-0.6B')
    registry=env['environment']['image_inventory'][binding['image']]
    spec={'entrypoint':str(a.entrypoint.resolve().relative_to(Path.cwd())),
          'entrypoint_sha256':sha(a.entrypoint),'image_id':registry['config_id'],
          'image_reference':binding['image'],'source':env['environment']['image_source_root'],
          'output':remote,'container_name':name,'model_path':model['path'],
          'model':model,
          'namespace':'close-'+env['identity'][:8]+'-'+a.case.lower()}
    specfile.write_text(json.dumps(spec,indent=2)+'\n')
    controller=root/'m01-remote.py'
    remote_parent=str(Path(remote).parent)
    remote_run(['mkdir','-p',remote_parent],check=True,timeout=30)
    subprocess.run(scp+[str(controller),str(specfile),'huawei-52:'+remote_parent+'/'],check=True,timeout=60)
    result=remote_run(['python3',remote_parent+'/m01-remote.py',remote_parent+'/'+specfile.name],timeout=1100)
    raw.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(scp+['-r','huawei-52:'+remote,str(raw)],check=True,timeout=180)
    # Entry invocation is real and recorded by the remote Docker exec receipt.
    record=read_json(raw/'receipt.json')
    assert any('/harness/'+spec['entrypoint'] in cmd for cmd in record['commands'])
    assert record['spec']==spec
    for path,expected in record['files'].items():
        assert sha(raw/path)==expected,path
    (attempt/(a.case+'-controller-hashes.json')).write_text(json.dumps({str(f):sha(f) for f in (Path(__file__),controller)},indent=2)+'\n')
    raise SystemExit(result.returncode)

if a.phase=='observe':
    record=read_json(raw/'receipt.json'); report=read_json(raw/'platform.json')
    installed=read_json(raw/'installed.json'); versions=read_json(raw/'versions.json')
    assert record['exit_code']==0 and not record['remaining_container_ids']
    assert record['remove_returncode']==0 and report['passed']
    assert installed['source_commit']==record['source_commit']==env['environment']['image_inventory'][binding['image']]['source_commit']
    direct=[read_json(raw/f'provider-{i}-before.json') for i in (0,1)]
    again=[read_json(raw/f'provider-{i}-repeat.json') for i in (0,1)]
    workers=[x['platform'] for x in report['workers']]
    uuids=lambda items:sorted(p['devices'][0]['uuid'] for p in items)
    assert len(set(uuids(workers)))==2
    assert all(x['platform_id']=='ascend' for x in direct+again+workers)
    assert versions==report['stack']['versions']
    # Installed inventory and independently enumerated providers are references,
    # rather than copies of the worker observation under test.
    measurements={
      'explicit_platform_selection':{'actual':report['explicit_cpu_selection']['platform_id'],'reference':'cpu'},
      'device_identity_stable':{'actual':uuids(again),'reference':uuids(direct)},
      'visible_device_filtering':{'actual':sorted(p['devices'][0]['ordinal'] for p in workers),'reference':[0,0]},
      'discovery_roundtrip':{'actual':uuids(workers),'reference':uuids(direct)},
      'vendor_version_recorded':{'actual':{n:workers[0]['software'][n] for n in versions},'reference':versions},
      'capability_unknown_is_not_supported':{
        'actual':[workers[0]['capabilities']['pd.transfer']['state'],report['required_capability_rejection']['discovery_files']],
        'reference':['unknown',0]}}
    assert report['required_capability_rejection']['exit_code']!=0
    assert report['unknown_platform_rejection']['code']=='unknown_platform'
    model=next(m for m in env['environment']['nodes'][0]['models'] if m['id']=='Qwen3-0.6B')
    modelmanifest={}
    # Full asset verification is performed in doctor, and tied to this attempt.
    pre=json.loads((attempt/'preflight.json').read_text())
    casepre=pre['cases'][a.case]
    model_checks=[x for x in casepre['checks'] if x['requirement']=='models']
    # M01 doesn't require model assets by contract. Still bind it explicitly to
    # the actual immutable model identity in the environment and run spec.
    if model_checks: assert all(x['passed'] for x in model_checks)
    asset=read_json(raw/'model-verification.json')
    assert asset['checked_files'] > 0 and asset['revision']==model['revision'],asset
    modelmanifest['huawei-52']={model['id']:asset['manifest_sha256']}
    spec=record['spec']
    imageinspect=read_json(raw/'image-inspect.json')[0]
    assert imageinspect['Id']==env['environment']['image_inventory'][binding['image']]['config_id']
    # Two independent TP1 workers, not one engine DP2 group.
    topology={'tp':len(workers[0]['devices']),'dp':1,'pp':1,'workers':len(workers)}
    obs={'identity':env['identity'],'installed_versions':versions,
         'execution_image':spec['image_reference'],'observed_topology':topology,
         'model_manifests':modelmanifest,'completed_operations':len(workers)+len(report['configuration_rejections'])+2,
         'resources_remaining':record['remaining_container_ids'],'measurements':measurements,
         'metrics':{'invalid_platform_rejections':int(report['unknown_platform_rejection']['code']=='unknown_platform')},
         'provenance':{'runtime_source':installed['source_commit'],'qualification_source':env['source']['head'],
                       'raw_receipt_sha256':sha(raw/'receipt.json'),'model_preflight_checks':len(model_checks)}}
    target=attempt/'cases'/a.case;target.mkdir(parents=True,exist_ok=True)
    (target/'observations.json').write_text(json.dumps(obs,indent=2)+'\n')
    print(json.dumps({'normalized':a.case,'worker_uuids':uuids(workers)}))

if a.phase=='cleanup':
    # Only observe absence; remote runner already removed the exact owned ID.
    r=remote_run(['env','DOCKER_HOST=unix:///run/dynamo-m10-docker.sock','/data/dynamo-m10/docker-bin/docker','ps','-aq','--filter','name=^/'+name+'$'],capture_output=True,text=True,check=True,timeout=45)
    proof={'observed_remaining_ids':r.stdout.split(),'container_name':name,'stderr':r.stderr}
    (attempt/(a.case+'-cleanup-observation.json')).write_text(json.dumps(proof,indent=2)+'\n')
    assert not proof['observed_remaining_ids'],proof
    print(json.dumps(proof))
