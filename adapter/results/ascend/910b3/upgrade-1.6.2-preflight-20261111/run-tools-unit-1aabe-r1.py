# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test installed tools packages with a tests-only mounted suite."""
import json,os,shutil,subprocess,hashlib,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');s=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111');w=r/'candidate-1aabe';out=w/'tools-unit-r1';out.mkdir()
u=out/'unit';u.mkdir()
files=['components/src/dynamo/profiler/tests/unit/'+x for x in ['test_dgd_platform.py','test_platform_profile.py','test_decode_interpolation_capacity.py','test_dgd_generation_planner_sla.py','test_dgd_materialization.py','test_planner_image_selection.py']]+['components/src/dynamo/planner/tests/unit/test_platform_resources.py','components/src/dynamo/planner/tests/unit/test_dgd_power_annotation.py']
for p in files:shutil.copy2(s/p,u/Path(p).name)
(u/'pytest.ini').write_text('[pytest]\nasyncio_mode=auto\nmarkers=\n    unit: unit\n    gpu_0: CPU\n    pre_merge: premerge\n')
(out/'test-source.json').write_text(json.dumps({p:hashlib.sha256((s/p).read_bytes()).hexdigest() for p in files},indent=2))
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(r/'docker-client-direct')}
tag=json.loads((w/'images.json').read_text())['tools'];argv=['docker','run','--name','heyi-upgrade162-49ef-tools-unit-r1','--network','none','--user','0','--mount',f'type=bind,src={u},dst=/unit,readonly','--mount',f'type=bind,src={w/"full-harness-r2"},dst=/harness,readonly','--mount',f'type=bind,src={out},dst=/evidence',tag['id'],'bash','/harness/run-tools.sh','/evidence/results']
record={'image':tag,'argv':argv,'started':time.time()};(out/'ownership.json').write_text(json.dumps(record,indent=2))
try:
 with (out/'run.log').open('x') as f:record['exit_code']=subprocess.run(argv,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=600).returncode
finally:
 cleanup=subprocess.run(['docker','rm','-f','heyi-upgrade162-49ef-tools-unit-r1'],env=env,capture_output=True,text=True);record['cleanup']={'code':cleanup.returncode,'stdout':cleanup.stdout,'stderr':cleanup.stderr};(out/'status.json').write_text(json.dumps(record,indent=2))
