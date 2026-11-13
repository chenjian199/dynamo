# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run installed interpolation APIs under a bounded owned Kubernetes Pod."""
import json,os,subprocess,time,hashlib
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');out=r/'profiling-r1';env={**os.environ,'KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'}
k=['kubectl','-n','heyi-upgrade162'];record={'started':time.time(),'source_commit':'49ef509336de2c6573dcffa018b33ed864d0c050','input_sha256':hashlib.sha256((out/'input.json').read_bytes()).hexdigest()}
try:
 deadline=time.monotonic()+1800
 while not (r/'distribution/tools-49ef-import-52.json').exists():
  if time.monotonic()>deadline:raise TimeoutError('tools import')
  time.sleep(10)
 assert json.loads((r/'distribution/tools-49ef-import-52.json').read_text())['passed']
 pod=json.loads((out/'tools.json').read_text());pod['spec']['containers'][0]['command']=['/opt/dynamo/tools-venv/bin/python','/offline/offline-profile.py','--input','/offline/input.json','--output','/offline/output'];pod['spec']['activeDeadlineSeconds']=3600
 (out/'run-pod.json').write_text(json.dumps(pod,indent=2))
 for f in ['capacity.json','rbac.json','run-pod.json']:
  v=subprocess.run(k+['apply','-f',str(out/f)],env=env,capture_output=True,text=True);(out/(f+'.apply.log')).write_text(v.stdout+v.stderr);v.check_returncode()
 name=pod['metadata']['name'];deadline=time.monotonic()+3650
 while True:
  actual=json.loads(subprocess.check_output(k+['get','pod',name,'-o','json'],env=env));(out/'pod-current.json').write_text(json.dumps(actual,indent=2));record['pod_uid']=actual['metadata']['uid']
  phase=actual['status']['phase']
  if phase in ['Succeeded','Failed']:break
  if time.monotonic()>deadline:raise TimeoutError('profile Pod deadline')
  time.sleep(15)
 record['phase']=phase
 logs=subprocess.run(k+['logs',name],env=env,capture_output=True,text=True);(out/'run.log').write_text(logs.stdout+logs.stderr)
 record['passed']=phase=='Succeeded' and (out/'output/profile-identity.json').is_file() and (out/'output/final_config.yaml').is_file()
except BaseException as e:record['error']=repr(e);raise
finally:
 record['finished']=time.time();(out/'result.json').write_text(json.dumps(record,indent=2))
