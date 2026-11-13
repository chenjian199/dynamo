# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bind all comparison arms to the same node, engine parameters and client."""
import copy,json,shlex
from pathlib import Path
r=Path(__file__).resolve().parent;root=r.parent;w=json.loads((r/'workload.json').read_text());ns='heyi-upgrade162';remote='/data/dynamo-upgrade-1.6.2-20261111/comparison-r1'
d=json.loads((root/'cluster-services/grove-kai-dgd.json').read_text())
d['metadata']={'name':'upgrade-compare','namespace':ns,'labels':{'heyi.io/campaign':'upgrade-1.6.2-ascend-20261111'},'annotations':{'nvidia.com/workload-provider':'component','nvidia.com/enable-grove':'false','nvidia.com/dynamo-discovery-backend':'kubernetes'}}
args=['--model',w['weights'],'--served-model-name',w['model'],'--tensor-parallel-size','1','--dtype',w['dtype'],'--enforce-eager','--disable-custom-all-reduce','--no-enable-prefix-caching','--max-model-len','2048','--max-num-seqs','8','--max-num-batched-tokens','2048','--kv-cache-memory-bytes',str(w['kv_cache_memory_bytes']),'--gpu-memory-utilization','0.2','--block-size','128']
for c in d['spec']['components']:
 c.pop('minAvailable',None);c['replicas']=1;p=c['podTemplate']['spec'];p['nodeSelector']={'kubernetes.io/hostname':'master','kubernetes.io/arch':'arm64'};main=p['containers'][0];main['env']=[e for e in main['env'] if e['name'] not in {'DYN_ROUTER_USE_KV_EVENTS','DYN_HEALTH_CHECK_PAYLOAD'}]
 main['args']=['python','-m','dynamo.frontend','--router-mode','round-robin'] if c['type']=='frontend' else ['python','-m','dynamo.vllm',*args]
 if c['type']!='frontend':
  main['resources']={'requests':{'cpu':'4','memory':'16Gi','huawei.com/Ascend910':'1'},'limits':{'cpu':'16','memory':'64Gi','huawei.com/Ascend910':'1'}}
 for v in p['volumes']:
  if v['name']=='models':v['hostPath']['path']='/data/dynamo-regression-models/Qwen3-0.6B'
for role,tag,version in [('old_heyi','heyi/dynamo:1.5.1-ascend0.23-baseline-bd801c6234-runtime','1.5.1'),('new_heyi','heyi/dynamo:1.6.2-ascend0.30-1aabe435ad-runtime','1.6.2')]:
 x=copy.deepcopy(d);x['metadata']['name']='upgrade-compare-'+role.replace('_','-')
 for c in x['spec']['components']:c['runtimeVersionOverride']=version;c['podTemplate']['spec']['containers'][0]['image']=tag
 (r/(role+'.json')).write_text(json.dumps(x,indent=2)+'\n')
worker=next(c for c in d['spec']['components'] if c['type']=='worker');pod=copy.deepcopy(worker['podTemplate']['spec']);pod.update(restartPolicy='Never',activeDeadlineSeconds=1800,automountServiceAccountToken=False);pod['volumes'].append({'name':'shm','emptyDir':{'medium':'Memory','sizeLimit':'8Gi'}});c=pod['containers'][0];c['volumeMounts'].append({'name':'shm','mountPath':'/dev/shm'});c['image']='quay.io/ascend/vllm-ascend:v0.30.0';c['command']=['bash','-lc'];c['args']=['source /usr/local/Ascend/ascend-toolkit/set_env.sh && exec '+shlex.join(['python3','-m','vllm.entrypoints.openai.api_server',*args,'--host','0.0.0.0','--port','8000'])];c['env']=[e for e in c['env'] if not e['name'].startswith('DYN_')];c['readinessProbe']={'httpGet':{'path':'/health','port':8000},'initialDelaySeconds':10,'periodSeconds':5,'failureThreshold':120}
x={'apiVersion':'v1','kind':'Pod','metadata':{'name':'upgrade-compare-native','namespace':ns,'labels':{'app':'upgrade-compare-native','heyi.io/campaign':'upgrade-1.6.2-ascend-20261111'}},'spec':pod};(r/'native.json').write_text(json.dumps(x,indent=2)+'\n')
s={'apiVersion':'v1','kind':'Service','metadata':{'name':'upgrade-compare-native','namespace':ns},'spec':{'selector':{'app':'upgrade-compare-native'},'ports':[{'port':8000,'targetPort':8000}]}};(r/'native-service.json').write_text(json.dumps(s,indent=2)+'\n')
t={'apiVersion':'v1','kind':'Pod','metadata':{'name':'upgrade-comparison-tools','namespace':ns,'labels':{'heyi.io/campaign':'upgrade-1.6.2-ascend-20261111'}},'spec':{'nodeSelector':{'kubernetes.io/hostname':'master-02-45d4a763'},'restartPolicy':'Never','activeDeadlineSeconds':7200,'automountServiceAccountToken':False,'containers':[{'name':'main','image':'heyi/dynamo:1.6.2-ascend0.30-49ef509336-tools','imagePullPolicy':'Never','command':['/opt/dynamo/bin/tools-entrypoint.sh'],'args':['sleep','infinity'],'env':[{'name':'DYN_PLATFORM','value':'cpu'},{'name':'TORCH_DEVICE_BACKEND_AUTOLOAD','value':'0'}],'resources':{'requests':{'cpu':'1','memory':'2Gi'},'limits':{'cpu':'4','memory':'8Gi'}},'volumeMounts':[{'name':'comparison','mountPath':'/comparison'}]}],'volumes':[{'name':'comparison','hostPath':{'path':remote,'type':'Directory'}}]}};(r/'tools.json').write_text(json.dumps(t,indent=2)+'\n')
