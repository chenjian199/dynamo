#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute canonical cross-node PD cases against the installed Operator."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

p=argparse.ArgumentParser();p.add_argument('spec');a=p.parse_args()
spec=json.loads(Path(a.spec).read_text());source=Path(spec['source']);out=Path(spec['output'])
out.mkdir(parents=True,exist_ok=False)
env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml','DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock'}
k=['kubectl','-n',spec['namespace']];docker='/data/dynamo-m10/docker-bin/docker'
record={'spec':spec,'started':time.time(),'commands':[],'owned_uid':None};proc=None

def call(cmd,name,timeout=120,check=True,data=None):
    record['commands'].append(cmd)
    result=subprocess.run(cmd,input=data,env=env,capture_output=True,timeout=timeout)
    (out/name).write_bytes(result.stdout)
    (out/(name+'.stderr')).write_bytes(result.stderr)
    if check and result.returncode:raise RuntimeError(f'{name}: exit {result.returncode}')
    return result

def kube(words,name,**kw):return call(k+words,name,**kw)
def read(name):return json.loads((out/name).read_text())
def write(name,value):(out/name).write_text(json.dumps(value,indent=2)+'\n')
def pods(label):
    data=json.loads(kube(['get','pods','-o','json'],label+'.json').stdout)
    return [x for x in data['items'] if x['metadata']['name'].startswith(spec['name']+'-')]

def observe_worker(pod):
    name=pod['metadata']['name'];uid=pod['metadata']['uid']
    command=['/opt/dynamo/bin/entrypoint.sh','python','-c',
      'import json,os,glob,torch,torch_npu,importlib.metadata as m; '
      'print(json.dumps({"versions":{n:m.version(n) for n in ("vllm","vllm-ascend","torch","torch-npu")},'
      '"count":torch.npu.device_count(),"available":torch.npu.is_available(),'
      '"name":torch.npu.get_device_name(0),"visible":os.environ.get("ASCEND_RT_VISIBLE_DEVICES"),'
      '"device_files":glob.glob("/dev/davinci*")}))']
    kube(['exec',name,'-c','main','--',*command],f'worker-{uid}-device.json')
    script=source/'adapter/modules/m10-validation/common/regression/check-installed.py'
    kube(['exec','-i',name,'-c','main','--','/opt/dynamo/bin/entrypoint.sh','python','-','--role','runtime'],f'worker-{uid}-installed.json',data=script.read_bytes())
    kube(['logs',name,'-c','main'],f'worker-{uid}.log')
    write(f'worker-{uid}-pod.json',pod)

try:
    assert hashlib.sha256((source/spec['entrypoint']).read_bytes()).hexdigest()==spec['entrypoint_sha256']
    result=kube(['get','dgd',spec['name'],'--ignore-not-found','-o','name'],'before.txt');assert not result.stdout.strip()
    kube(['get','deploy','heyi-upgrade162-dynamo-operator-controller-manager','-o','json'],'operator.json')
    operator=read('operator.json')
    assert any(c['image']==spec['operator_image'] for c in operator['spec']['template']['spec']['containers'])
    node=json.loads(kube(['get','node',spec['kubernetes_nodes'][0],'-o','json'],'placement-node.json').stdout)
    node_label=node['metadata']['labels']['kubernetes.io/hostname']
    second=json.loads(kube(['get','node',spec['kubernetes_nodes'][1],'-o','json'],'placement-node-2.json').stdout)
    second_label=second['metadata']['labels']['kubernetes.io/hostname']
    base=['python3',str(source/'adapter/modules/m08-kubernetes/common/cluster/render.py'),
      '--platform','ascend','--mode','pd','--namespace',spec['namespace'],'--name',spec['name'],
      '--node',node_label,'--model-host-path',spec['model_path'],
      '--second-node',second_label,'--second-model-host-path',spec['remote_model_path']]
    call(base,'rendered.json');manifest=read('rendered.json')
    for component in manifest['spec']['components']:
        component['runtimeVersionOverride']='1.6.2'
        component['replicas']=1
        c=component['podTemplate']['spec']['containers'][0];c['image']=spec['image_reference']
        c['env'].append({'name':'OMP_NUM_THREADS','value':'4'})
        if component['type']!='frontend':
            argv=c['args'];i=argv.index('--gpu-memory-utilization');del argv[i:i+2]
            argv+=['--kv-cache-memory-bytes','1073741824']
    write('manifest.json',manifest)
    for i,model in enumerate(spec['models']):call(['python3',str(source/'adapter/workflow/assets.py'),json.dumps(model)],f'model-{i}.json',1800)
    kube(['create','-f',str(out/'manifest.json')],'create.txt')
    dgd=json.loads(kube(['get','dgd',spec['name'],'-o','json'],'created.json').stdout)
    record['owned_uid']=dgd['metadata']['uid'];write('ownership.json',record)
    cmd=['python3',str(source/spec['entrypoint']),'--namespace',spec['namespace'],'--name',spec['name'],
         '--mode','pd','--client',str(source/'adapter/workflow/regression/cluster/client.py'),'--output',str(out/'regression')]
    record['commands'].append(cmd);deadline=time.monotonic()+5500;observed=set()
    with (out/'regress.log').open('x') as log:
        proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=env)
        while proc.poll() is None:
            for pod in pods('latest-pods'):
                uid=pod['metadata']['uid']
                if uid in observed or not any('-'+role+'-' in pod['metadata']['name'] for role in ('prefill','decode')) or pod['metadata'].get('deletionTimestamp'):continue
                ready=any(c['type']=='Ready' and c['status']=='True' for c in pod.get('status',{}).get('conditions',[]))
                if ready:observe_worker(pod);observed.add(uid)
            if time.monotonic()>deadline:raise TimeoutError('Bounded Operator regression')
            time.sleep(5)
    assert proc.returncode==0,proc.returncode
    assert observed,'No ready worker device inspection'
    record['observed_workers']=sorted(observed)
    final=pods('handoff-pods')
    frontend=next(p for p in final if p['metadata'].get('labels',{}).get('nvidia.com/dynamo-component')=='frontend')
    dg=read('regression/dgd.json');namespace=dg['status']['components']['decode']['runtimeNamespace']
    handoff=source/'adapter/modules/m05-pd-transfer/common/transfer/handoff.py'
    kube(['exec','-i',frontend['metadata']['name'],'-c','main','--','/opt/dynamo/bin/entrypoint.sh','python','-',
          '--namespace',namespace,'--discovery-backend','kubernetes','--model','m08-model','--case','valid','--timeout','120',
          '--output','/tmp/closure-handoff.json'],'handoff.log',timeout=300,data=handoff.read_bytes())
    kube(['exec',frontend['metadata']['name'],'-c','main','--','cat','/tmp/closure-handoff.json'],'handoff.json')

    kube(['get','dgd',spec['name'],'-o','json'],'final-dgd.json')
    record['exit_code']=0
except Exception as e:record.update(exit_code=1,error=repr(e))
finally:
    if proc and proc.poll() is None:
        proc.terminate()
        try:proc.wait(timeout=30)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
    if record['owned_uid']:
        try:
            for pod in pods('cleanup-pods'):
                kube(['logs',pod['metadata']['name'],'-c','main'],'cleanup-'+pod['metadata']['uid']+'.log',check=False)
            write('delete.json',{'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':record['owned_uid']}})
            kube(['delete','--raw','/apis/nvidia.com/v1beta1/namespaces/'+spec['namespace']+'/dynamographdeployments/'+spec['name'],'-f',str(out/'delete.json')],'delete.log')
            deadline=time.monotonic()+240
            while True:
                remaining=pods('remaining-pods')
                dgd=kube(['get','dgd',spec['name'],'--ignore-not-found','-o','name'],'remaining-dgd.txt')
                if not remaining and not dgd.stdout.strip():break
                if time.monotonic()>deadline:raise TimeoutError('Owned DGD did not clean up')
                time.sleep(3)
        except Exception as e:record['cleanup_error']=repr(e);record['exit_code']=1
    remaining=pods('after-pods');record['resources_remaining']=[x['metadata']['uid'] for x in remaining]
    if kube(['get','dgd',spec['name'],'--ignore-not-found','-o','name'],'after-dgd.txt').stdout.strip():record['resources_remaining'].append(spec['name'])
    record['finished']=time.time();record['files']={str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in out.rglob('*') if f.is_file()}
    write('receipt.json',record)
print(json.dumps({'exit_code':record['exit_code'],'error':record.get('error'),'cleanup_error':record.get('cleanup_error')}))
raise SystemExit(record['exit_code'] or bool(record['resources_remaining']))
