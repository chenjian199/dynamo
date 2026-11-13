#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run a bound M01 probe against installed wheels; retain raw independent inventory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('spec')
a = p.parse_args()
spec = json.loads(Path(a.spec).read_text())
out = Path(spec['output']); out.mkdir(parents=True, exist_ok=False)
source = Path(spec['source'])
manifest = json.loads((source/'SOURCE.json').read_text())
entry = spec['entrypoint']
assert hashlib.sha256((source/entry).read_bytes()).hexdigest() == spec['entrypoint_sha256']
assert manifest['files'][entry] == spec['entrypoint_sha256']
docker = '/data/dynamo-m10/docker-bin/docker'
env = {**os.environ, 'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock'}
name = spec['container_name']; cid = None
record = {'spec':spec, 'started':time.time(), 'source_commit':manifest['commit'], 'commands':[]}

def call(argv, file=None, timeout=60, check=True):
    record['commands'].append(argv)
    r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
    if file:
        (out/file).write_text(r.stdout)
        (out/(file+'.stderr')).write_text(r.stderr)
    if check and r.returncode:
        raise RuntimeError(f'{argv[:4]}: exit {r.returncode}: {r.stderr[-1800:]}')
    return r

def inside(argv, file=None, timeout=60, check=True):
    return call([docker,'exec',cid,'/opt/dynamo/bin/entrypoint.sh',*argv],file,timeout,check)

try:
    call(['python3',str(source/'adapter/workflow/assets.py'),json.dumps(spec['model'])], 'model-verification.json', 1800)
    before = call(['npu-smi','info'],'npu-before.txt').stdout
    for d in (0,1):
        assert f'No running processes found in NPU {d}' in before
    inspected = json.loads(call([docker,'image','inspect',spec['image_id']], 'image-inspect.json').stdout)[0]
    assert inspected['Id'] == spec['image_id']
    argv = [docker,'run','-d','--name',name,'--network','host','--shm-size','8g','--user','0',
            '-e','HF_HUB_OFFLINE=1','-e','OMP_NUM_THREADS=4','-e','PYTHONUNBUFFERED=1',
            '--mount',f'type=bind,src={source},dst=/harness,readonly',
            '--mount',f'type=bind,src={out},dst=/evidence',
            '--mount',f"type=bind,src={spec['model_path']},dst=/models/Qwen3-0.6B,readonly"]
    for d in ('davinci0','davinci1','davinci_manager','devmm_svm','hisi_hdc'):
        argv += ['--device','/dev/'+d]
    for d in ('/usr/local/Ascend/driver','/usr/local/dcmi','/usr/local/sbin/npu-smi'):
        argv += ['--mount',f'type=bind,src={d},dst={d},readonly']
    argv += [spec['image_id'],'sleep','infinity']
    cid = call(argv).stdout.strip(); record['container_id']=cid
    (out/'ownership.json').write_text(json.dumps(record,indent=2)+'\n')
    inside(['python','/harness/adapter/modules/m10-validation/common/regression/check-installed.py','--role','runtime'],'installed.json',120)
    direct = 'import json; from dynamo.common.platform import PlatformRegistry; print(json.dumps(PlatformRegistry().prepare("ascend")))'
    for repeat in ('before','repeat'):
        for d in (0,1):
            inside(['env',f'ASCEND_RT_VISIBLE_DEVICES={d}','python','-c',direct],f'provider-{d}-{repeat}.json',120)
    worker_env = ['DYN_NAMESPACE='+spec['namespace'], 'MODEL_PATH=/models/Qwen3-0.6B',
                  'WORKER_GPU1=0','WORKER_GPU2=1','DYN_HTTP_PORT=18301',
                  'DYN_SYSTEM_PORT1=18302','DYN_SYSTEM_PORT2=18303',
                  'KV_EVENT_PORT1=18304','KV_EVENT_PORT2=18305','FPM_PORT1=18306','FPM_PORT2=18307',
                  'DYN_FILE_KV=/tmp/closure-m01-discovery']
    # Redirection is literal here; no interpolated shell fragments.
    inside(['env',*worker_env,'bash','-c',
            'setsid bash /harness/adapter/platforms/ascend/bringup/ascend-kv-serve.sh --tensor-parallel-size 1 --data-parallel-size 1 --pipeline-parallel-size 1 > /evidence/workers.log 2>&1 & echo $! > /evidence/worker-group.pid'], 'launch.txt')
    ready = '''import json,time,urllib.request
for _ in range(240):
 try:
  with urllib.request.urlopen("http://127.0.0.1:18301/v1/models/Qwen3-0.6B/ready",timeout=2) as r:
   data=json.load(r)
  if data.get("ready"):
   print(json.dumps(data)); break
 except Exception: pass
 time.sleep(2)
else: raise TimeoutError("M01 two-worker readiness")
'''
    inside(['python','-c',ready],'ready.json',510)
    inside(['python','/harness/'+entry,'--platform','ascend','--namespace',spec['namespace'],
            '--discovery-dir','/tmp/closure-m01-discovery','--model-path','/models/Qwen3-0.6B',
            '--probe-gpu','0','--output','/evidence/platform.json'],'probe.log',240)
    # Freeze actual installed package versions separately from the profile expectation.
    inside(['python','-c','import json,importlib.metadata as m; print(json.dumps({n:m.version(n) for n in ("vllm","vllm-ascend","torch","torch-npu")}))'],'versions.json')
    record['exit_code']=0
except Exception as e:
    record['exit_code']=1; record['error']=repr(e)
finally:
    if cid:
        # SIGTERM reaches the launch group. Observe fatal shutdown diagnostics before removal.
        inside(['bash','-c','if test -s /evidence/worker-group.pid; then kill -TERM -- "-$(cat /evidence/worker-group.pid)"; fi'],'stop.txt',check=False)
        time.sleep(15)
        stopped=call([docker,'rm','-f',cid],'remove.txt',120,False)
        record['remove_returncode']=stopped.returncode
    remaining=call([docker,'ps','-aq','--filter','name=^/'+name+'$'],'absence.txt').stdout.split()
    record['remaining_container_ids']=remaining
    call(['npu-smi','info'],'npu-after.txt')
    if (out/'workers.log').exists():
        fatal_lines=[line for line in (out/'workers.log').read_text(errors='replace').splitlines() if 'FATAL' in line or 'panicked at' in line]
        record['fatal_shutdown_lines']=fatal_lines
        if fatal_lines: record['exit_code']=1
    record['finished']=time.time()
    record['files']={str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in out.rglob('*') if f.is_file()}
    (out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'exit_code':record['exit_code'],'output':str(out),'error':record.get('error')}),flush=True)
raise SystemExit(record['exit_code'] or bool(remaining))
