# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run independent packaged-image regression cases, each in its own owned container."""
import json
import os
from pathlib import Path
import subprocess
import time

root=Path('/data/dynamo-upgrade-1.6.2-20261111');work=root/'candidate-5cbe'
harness=work/'full-harness-r3';out=work/'kvbm-r4';out.mkdir()
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(root/'docker-client-direct')}
image=json.loads((work/'images.json').read_text())['runtime']; records=[]
model='/models/Qwen3-0.6B'; cache='/harness/adapter/modules/m06-kvbm-collectives/common/cache/run.py'; pd='/harness/adapter/modules/m05-pd-transfer/common/transfer/run.py'
def case(name, devices, command, limit):
    record={'name':name,'image':image,'argv':command,'limit_seconds':limit,'started':time.time()}
    cid=None
    try:
        health=subprocess.check_output(['npu-smi','info'],text=True,timeout=30)
        (out/(name+'.npu-before.txt')).write_text(health)
        for device in devices:
            assert f'No running processes found in NPU {device}' in health
        argv=['docker','run','-d','--name','heyi-upgrade-162-r4-'+name,'--network','host','--shm-size','16g','--user','0',
              '-e','OMP_NUM_THREADS=4','-e','HF_HUB_OFFLINE=1','-e','PYTHONUNBUFFERED=1',
              '-e','PYTHONPATH=/harness/test-deps','-e','PYTORCH_NPU_ALLOC_CONF=expandable_segments:True',
              '--mount',f'type=bind,src={harness},dst=/harness,readonly','--mount',f'type=bind,src={out},dst=/evidence',
              '--mount','type=bind,src=/data/weight/Qwen3-0.6B,dst=/models/Qwen3-0.6B,readonly']
        for d in devices:argv+=['--device',f'/dev/davinci{d}']
        for p in ['/dev/davinci_manager','/dev/devmm_svm','/dev/hisi_hdc']:argv+=['--device',p]
        for p in ['/usr/local/Ascend/driver','/usr/local/dcmi','/usr/local/sbin/npu-smi']:argv+=['--mount',f'type=bind,src={p},dst={p},readonly']
        argv += [image['id'],'sleep','infinity']
        cid=subprocess.check_output(argv,env=env,text=True).strip();record['container_id']=cid;record['create_argv']=argv
        (out/(name+'.ownership.json')).write_text(json.dumps(record,indent=2)+'\n')
        with (out/(name+'.installed.json')).open('x') as f:
            subprocess.run(['docker','exec',cid,'/opt/dynamo/bin/entrypoint.sh','python','/harness/check-installed.py','--role','runtime'],env=env,stdout=f,check=True,timeout=90)
        with (out/(name+'.log')).open('x') as f:
            result=subprocess.run(['docker','exec','-w','/harness',cid,'/opt/dynamo/bin/entrypoint.sh','timeout',str(limit),*command],env=env,stdout=f,stderr=subprocess.STDOUT,timeout=limit+60)
        record['exit_code']=result.returncode
    except Exception as error:
        record['error']=repr(error)
    finally:
        if cid:
            cleanup=subprocess.run(['docker','rm','-f',cid],env=env,capture_output=True,text=True,timeout=60)
            record['cleanup']={'exit_code':cleanup.returncode,'stdout':cleanup.stdout,'stderr':cleanup.stderr}
        record['finished']=time.time();records.append(record)
        (out/'status.json').write_text(json.dumps({'source_commit':'5cbe424879e88f3da56e3f99054f8bcfde49165c','cases':records,'certified':False},indent=2)+'\n')
    return record.get('exit_code')==0 and record.get('cleanup',{}).get('exit_code')==0

# Wait for the tests-only harness, not an editable implementation overlay.
deadline=time.monotonic()+900
while not (harness/'SOURCE.json').exists():
    if time.monotonic()>deadline:raise TimeoutError('test harness not ready')
    time.sleep(5)
base=['python',cache,'--platform','ascend','--devices','0,1','--tp','2','--model',model,'--disable-custom-all-reduce']
if case('kvbm-baseline',[6,7],base+['--baseline','--output','/evidence/kvbm-baseline'],1200):
    for tier in ['host','disk']:
        command=base+['--cancel','--cycles','2','--baseline-summary','/evidence/kvbm-baseline/summary.json','--output','/evidence/kvbm-'+tier]
        if tier=='disk':command+=['--disk']
        case('kvbm-'+tier,[6,7],command,1800)
