#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prepare unsealed real cross-node KV readback recipe; never synthesize bytes."""
from pathlib import Path
r=Path(__file__).resolve().parent
s=(r/'cross-case-remote.py').read_text().replace('import time\n','import time\nimport uuid\nimport tarfile\n')
s=s.replace("'owned_uid':None};proc=None", "'owned_uid':None};proc=None;cm_uid=None\nnonce=uuid.uuid4().hex;cm_name=spec['name']+'-byte-observer'")
s=s.replace("    base=['python3'", "    observer=Path(a.spec).parent/'kv_byte_observer.py'\n    assert hashlib.sha256(observer.read_bytes()).hexdigest()==spec['observer_sha256']\n    cm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':cm_name,'namespace':spec['namespace']},'data':{'kv_byte_observer.py':observer.read_text()}}\n    write('observer-configmap.json',cm)\n    result=kube(['create','-f',str(out/'observer-configmap.json'),'-o','json'],'observer-created.json');cm_uid=json.loads(result.stdout)['metadata']['uid']\n    record['observer_uid']=cm_uid;write('ownership.json',record)\n    base=['python3'",1)
s=s.replace("            argv+=['--kv-cache-memory-bytes','1073741824']", """            argv+=['--kv-cache-memory-bytes','1073741824','--worker-extension-cls','kv_byte_observer.KVByteObserver']
            c['env'] += [{'name':'PYTHONPATH','value':'/observer'},
                         {'name':'CLOSURE_KV_AUDIT_DIR','value':'/evidence-kv'},
                         {'name':'CLOSURE_KV_AUDIT_ROLE','value':component['name']},
                         {'name':'CLOSURE_KV_AUDIT_NONCE','value':nonce}]
            c.setdefault('volumeMounts',[]).extend([{'name':'byte-observer','mountPath':'/observer','readOnly':True}, {'name':'byte-evidence','mountPath':'/evidence-kv'}])
            component['podTemplate']['spec'].setdefault('volumes',[]).extend([{'name':'byte-observer','configMap':{'name':cm_name}}, {'name':'byte-evidence','emptyDir':{}}])""")
s=s.replace("cmd=['python3',str(source/spec['entrypoint']),'--namespace'", "cmd=['python3',str(source/'adapter/modules/m08-kubernetes/common/cluster/cross.py'),'--namespace'")
marker="    handoff=source/'adapter/modules/m05-pd-transfer/common/transfer/handoff.py'"
insert="""    selected={role:next(p for p in final if p['metadata'].get('labels',{}).get('nvidia.com/dynamo-component')==role) for role in ('prefill','decode')}
    kube(['exec',selected['prefill']['metadata']['name'],'-c','main','--','cat','/evidence-kv/source-ready.json'],'byte-source-ready.json')
    kube(['exec',selected['decode']['metadata']['name'],'-c','main','--','python','-c',
          'from pathlib import Path; p=Path("/evidence-kv"); (p/"peer").write_text('+repr(selected['prefill']['status']['podIP'])+'); (p/"armed").touch()'], 'byte-arm.log')
"""
s=s.replace(marker,insert+marker)
marker="    kube(['get','dgd',spec['name'],'-o','json'],'final-dgd.json')"
insert="""    kube(['exec',selected['decode']['metadata']['name'],'-c','main','--','rm','/evidence-kv/armed'],'byte-disarm.log')
    for role,pod in selected.items():
        kube(['exec',pod['metadata']['name'],'-c','main','--','tar','-cf','-','-C','/evidence-kv','.'],f'kv-{role}.tar',timeout=180)
        target=out/('kv-'+role);target.mkdir()
        with tarfile.open(out/f'kv-{role}.tar') as archive:
            for member in archive.getmembers():
                assert (target/member.name).resolve().is_relative_to(target) and (member.isfile() or member.isdir())
            archive.extractall(target)
"""
s=s.replace(marker,insert+marker)
marker="    remaining=pods('after-pods');record['resources_remaining']"
insert="""    if cm_uid:
        try:
            write('observer-delete.json',{'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':cm_uid}})
            kube(['delete','--raw','/api/v1/namespaces/'+spec['namespace']+'/configmaps/'+cm_name,'-f',str(out/'observer-delete.json')],'observer-delete.log')
            assert not kube(['get','configmap',cm_name,'--ignore-not-found','-o','name'],'observer-absence.txt').stdout.strip()
        except Exception as e:record['observer_cleanup_error']=repr(e);record['exit_code']=1
"""
s=s.replace(marker,insert+marker)
(r/'kvbyte-case-remote.py').write_text(s)
s=(r/'cross-case-controller.py').read_text().replace('cross-case-remote.py','kvbyte-case-remote.py').replace('normalize-cross-case.py','normalize-kvbyte-case.py')
s=s.replace("'namespace':'heyi-upgrade162'}", "'namespace':'heyi-upgrade162','observer_sha256':sha(root/'kv_byte_observer.py')}")
s=s.replace("str(controller),str(specfile),'huawei-52:'", "str(controller),str(specfile),str(root/'kv_byte_observer.py'),'huawei-52:'")
s=s.replace("    remote_model=next", "    for node in binding['nodes']:\n        cmd=['ssh','-F',env['environment']['ssh_config'],node,'cat /sys/class/dmi/id/product_uuid']\n        with (attempt/(a.case+'-'+node+'-dmi.txt')).open('x') as f:subprocess.run(cmd,stdout=f,check=True,timeout=60)\n    remote_model=next",1)
s=s.replace("assert any(str(Path(spec['source'])/spec['entrypoint']) in cmd for cmd in record['commands'])", "assert any(any(str(arg).endswith('/handoff.py') for arg in cmd) or (cmd[-1]=='--timeout' if cmd else False) for cmd in record['commands']) if False else record['exit_code']==0")
# Handoff is piped stdin to the frontend, so explicitly record and compare script bytes separately.
s=s.replace("    assert any(any(str(arg).endswith('/handoff.py') for arg in cmd) or (cmd[-1]=='--timeout' if cmd else False) for cmd in record['commands']) if False else record['exit_code']==0", "    assert sha(raw/'handoff-entrypoint.py')==spec['entrypoint_sha256']")
(r/'kvbyte-case-controller.py').write_text(s)
s=(r/'kvbyte-case-remote.py').read_text().replace("    kube(['exec','-i',frontend['metadata']['name']", "    (out/'handoff-entrypoint.py').write_bytes(handoff.read_bytes())\n    kube(['exec','-i',frontend['metadata']['name']",1)
(r/'kvbyte-case-remote.py').write_text(s)
s=(r/'normalize-cross-case.py').read_text()
marker="obs={'identity':env['identity']"
insert="""assert not record.get('observer_cleanup_error')
assert not (raw/'observer-absence.txt').read_text().strip()
assert sha(raw/'handoff-entrypoint.py')==record['spec']['entrypoint_sha256']
transfers=sorted((raw/'kv-decode').glob('transfer-*.json'));assert transfers
producer=attempt/'cases'/a.case/'producer.bin';consumer=producer.with_name('consumer.bin');producer.parent.mkdir(parents=True,exist_ok=True)
bytes_count=0
with producer.open('xb') as pfile, consumer.open('xb') as cfile:
    for path in transfers:
        transfer=read(path);assert transfer['passed'] and transfer['native_returncode']==0
        pp=path.with_suffix('.producer.bin');cp=path.with_suffix('.consumer.bin')
        assert sha(pp)==transfer['producer_sha256'] and sha(cp)==transfer['consumer_sha256']
        assert pp.stat().st_size==cp.stat().st_size==sum(transfer['sizes'])==transfer['transferred_bytes']
        pfile.write(pp.read_bytes());cfile.write(cp.read_bytes());bytes_count+=transfer['transferred_bytes']
assert bytes_count>0
machines=[(attempt/(a.case+'-'+node+'-dmi.txt')).read_text().strip() for node in binding['nodes']]
assert all(machines) and len(set(machines))==2
measurements['transport_evidence']={'actual':{'connector':params['_dynamo_pd_contract']['connector'],'native_api':list({read(f)['native_api'] for f in transfers})},'reference':{'connector':'MooncakeConnector','native_api':['batch_transfer_sync_read']}}
"""
s=s.replace(marker,insert+marker)
s=s.replace("'completed_operations':len(summary['requests'])+1", "'node_machine_ids':machines,'files':{'producer':str(producer),'consumer':str(consumer)},'completed_operations':len(summary['requests'])+1")
(r/'normalize-kvbyte-case.py').write_text(s)
for name in ['kvbyte-case-remote.py','kvbyte-case-controller.py','normalize-kvbyte-case.py']:
 compile((r/name).read_text(),str(r/name),'exec')
