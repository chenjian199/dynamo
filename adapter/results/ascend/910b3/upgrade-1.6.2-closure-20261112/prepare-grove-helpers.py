#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prepare a new unsealed compound gang-barrier and cross-node PD recipe."""
from pathlib import Path
r=Path(__file__).resolve().parent
s=(r/'cross-case-remote.py').read_text()
s=s.replace('record={\'spec\':spec,\'started\':time.time(),\'commands\':[],\'owned_uid\':None};proc=None', 'record={\'spec\':spec,\'started\':time.time(),\'commands\':[],\'owned_uid\':None};proc=None\naux_name=spec["name"]+"-barrier";aux_uid=None')
marker='try:\n    assert hashlib.sha256'
helper='''def cleanup_aux():
    if not aux_uid:
        return
    write('barrier-delete.json', {'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':aux_uid}})
    kube(['delete','--raw','/apis/nvidia.com/v1beta1/namespaces/'+spec['namespace']+'/dynamographdeployments/'+aux_name,
          '-f',str(out/'barrier-delete.json')],'barrier-delete.log')
    deadline=time.monotonic()+240
    while True:
        data=json.loads(kube(['get','pods','-o','json'],'barrier-cleanup-pods.json').stdout)
        remaining=[p for p in data['items'] if p['metadata']['name'].startswith(aux_name+'-')]
        if not remaining:break
        if time.monotonic()>deadline:raise TimeoutError('Gang auxiliary cleanup')
        time.sleep(3)

'''
s=s.replace(marker,helper+marker)
s=s.replace("    write('manifest.json',manifest)","    manifest['metadata']['annotations'].update({'nvidia.com/workload-provider':'grove','nvidia.com/enable-grove':'true','nvidia.com/kai-scheduler-queue':'default-queue'})\n    write('manifest.json',manifest)")
marker="    kube(['create','-f',str(out/'manifest.json')],'create.txt')"
barrier='''    # Auxiliary single-node 2-worker barrier is separately recorded; final topology
    # is the cross-node 1P1D service below, never relabel the auxiliary deployment.
    call(['python3',str(source/'adapter/modules/m08-kubernetes/common/cluster/render.py'),
          '--platform','ascend','--mode','aggregate','--namespace',spec['namespace'],
          '--name',aux_name,'--node',node_label,'--model-host-path',spec['model_path']], 'barrier-rendered.json')
    auxiliary=read('barrier-rendered.json')
    auxiliary['metadata']['annotations'].update({'nvidia.com/workload-provider':'grove','nvidia.com/enable-grove':'true','nvidia.com/kai-scheduler-queue':'default-queue'})
    for component in auxiliary['spec']['components']:
        component['runtimeVersionOverride']='1.6.2'
        component['replicas']=1 if component['type']=='frontend' else 2
        c=component['podTemplate']['spec']['containers'][0];c['image']=spec['image_reference']
        if component['type']!='frontend':
            argv=c['args'];i=argv.index('--gpu-memory-utilization');del argv[i:i+2]
            argv+=['--kv-cache-memory-bytes','1073741824']
    write('barrier-manifest.json',auxiliary)
    assert not kube(['get','dgd',aux_name,'--ignore-not-found','-o','name'],'barrier-before.txt').stdout.strip()
    assert not kube(['get','pod',aux_name+'-capacity-hold','--ignore-not-found','-o','name'],'hold-before.txt').stdout.strip()
    try:
        call(['python3',str(source/spec['entrypoint']),'--manifest',str(out/'barrier-manifest.json'),
              '--output',str(out/'barrier')],'barrier.log',600)
    finally:
        result=kube(['get','dgd',aux_name,'--ignore-not-found','-o','json'],'barrier-created.json')
        if result.stdout.strip():aux_uid=json.loads(result.stdout)['metadata']['uid'];record['barrier_uid']=aux_uid;write('ownership.json',record)
    # After the holding Pod disappears, both workers must actually serve requests.
    deadline=time.monotonic()+1200
    while True:
        dg=json.loads(kube(['get','dgd',aux_name,'-o','json'],'barrier-ready-dgd.json').stdout)
        pp=json.loads(kube(['get','pods','-o','json'],'barrier-ready-pods.json').stdout)['items']
        worker_name=next(c['name'] for c in auxiliary['spec']['components'] if c['type']=='worker')
        selected=[p for p in pp if p['metadata'].get('labels',{}).get('nvidia.com/dynamo-graph-deployment-name')==aux_name]
        workers=[p for p in selected if p['metadata'].get('labels',{}).get('nvidia.com/dynamo-component')==worker_name]
        ready=lambda p:any(c['type']=='Ready' and c['status']=='True' for c in p.get('status',{}).get('conditions',[]))
        if len(workers)==2 and all(ready(p) for p in selected) and len(selected)==3:break
        if time.monotonic()>deadline:raise TimeoutError('Gang release did not become ready')
        time.sleep(5)
    frontend=next(p for p in selected if p['metadata'].get('labels',{}).get('nvidia.com/dynamo-component')=='frontend')
    runtime_ns=dg['status']['components'][worker_name]['runtimeNamespace']
    kube(['exec','-i',frontend['metadata']['name'],'-c','main','--','/opt/dynamo/bin/entrypoint.sh','python','-',
          '--url','http://127.0.0.1:8000','--model','m08-model','--namespace',runtime_ns,'--workers','2'],
          'barrier-api.log',1200,data=(source/'adapter/workflow/regression/cluster/client.py').read_bytes())
    kube(['get','podcliquesets','-o','json'],'barrier-podcliquesets.json')
    kube(['get','podgroups.scheduling.run.ai','-o','json'],'barrier-podgroups.json')
    cleanup_aux();aux_uid=None
'''
s=s.replace(marker,barrier+marker)
s=s.replace("cmd=['python3',str(source/spec['entrypoint']),'--namespace'", "cmd=['python3',str(source/'adapter/modules/m08-kubernetes/common/cluster/cross.py'),'--namespace'")
s=s.replace('finally:\n    if proc and proc.poll() is None:', '''finally:
    if aux_uid:
        try:cleanup_aux();aux_uid=None
        except Exception as e:record['aux_cleanup_error']=repr(e);record['exit_code']=1
    if proc and proc.poll() is None:''')
(r/'grove-case-remote.py').write_text(s)
s=(r/'cross-case-controller.py').read_text().replace('cross-case-remote.py','grove-case-remote.py').replace('normalize-cross-case.py','normalize-grove-case.py')
(r/'grove-case-controller.py').write_text(s)
s=(r/'normalize-cross-case.py').read_text().replace('assert record[\'exit_code\']==0','assert not record.get("aux_cleanup_error")\nassert record[\'exit_code\']==0')
marker="obs={'identity':env['identity']"
extra='''barrier=read(raw/'barrier/barrier.json');assert barrier['passed'] and barrier['held']==barrier['capacity']-1
aux=read(raw/'barrier-manifest.json');aux_name=aux['metadata']['name']
name=next(c['name'] for c in aux['spec']['components'] if c['type']=='worker')
for i in range(6):
    snap=read(raw/f'barrier/blocked-{i}.pods.json')
    group=[p for p in snap['items'] if p['metadata']['name'].startswith(aux_name+'-0-'+name.lower()+'-')]
    assert len(group)==2
    assert all(p['spec']['schedulerName']=='kai-scheduler' and not p['spec'].get('nodeName') for p in group)
ready=read(raw/'barrier-ready-pods.json')['items']
workers=[p for p in ready if p['metadata'].get('labels',{}).get('nvidia.com/dynamo-graph-deployment-name')==aux_name and p['metadata'].get('labels',{}).get('nvidia.com/dynamo-component')==name]
assert len(workers)==2
assert all(p['spec']['schedulerName']=='kai-scheduler' and p['spec']['nodeName']==binding['kubernetes_nodes'][0] for p in workers)
assert all(any(c['type']=='Ready' and c['status']=='True' for c in p['status']['conditions']) for p in workers)
api=json.loads(next(line.split('=',1)[1] for line in (raw/'barrier-api.log').read_text().splitlines() if line.startswith('REGRESSION_RESULT=')))
assert api['passed'] and api['sse_parity'] and api['post_cancel_recovery']
assert not any(p['metadata']['name'].startswith(aux_name+'-') for p in read(raw/'barrier-cleanup-pods.json')['items'])
measurements['gang_scheduling']={'actual':{'blocked_workers':2,'ready_workers':len(workers),'scheduler':workers[0]['spec']['schedulerName']},'reference':{'blocked_workers':next(c['replicas'] for c in aux['spec']['components'] if c['type']=='worker'),'ready_workers':next(c['replicas'] for c in aux['spec']['components'] if c['type']=='worker'),'scheduler':'kai-scheduler'}}
'''
s=s.replace(marker,extra+marker)
s=s.replace("'completed_operations':len(summary['requests'])+1", "'metrics':{'owned_pods_after_cleanup':len(record['resources_remaining'])},'completed_operations':len(summary['requests'])+1")
(r/'normalize-grove-case.py').write_text(s)
s=(r/'start-cross-2a239c2b50.py').read_text()
s=s.replace('upgrade162-operator-','upgrade162-asymmetric-').replace('upgrade162-cross-','upgrade162-grove-').replace('cross-launch-','grove-launch-').replace('operator-launch-','asymmetric-launch-').replace("f'cross-{sequence", "f'grove-{sequence").replace('operator-scope.json','asymmetric-scope.json').replace('operator-recipes.json','asymmetric-recipes.json').replace('M08-F02','M08-F03').replace("[(1,'cross.py')]","[(1,'gang.py')]").replace('cross-case-controller.py','grove-case-controller.py').replace('common/cluster/','common/advanced/').replace('cross-scope.json','grove-scope.json').replace('cross-recipes.json','grove-recipes.json')
(r/'start-grove-2a239c2b50.py').write_text(s)
for name in ['grove-case-remote.py','grove-case-controller.py','normalize-grove-case.py','start-grove-2a239c2b50.py']:
 compile((r/name).read_text(),str(r/name),'exec')
