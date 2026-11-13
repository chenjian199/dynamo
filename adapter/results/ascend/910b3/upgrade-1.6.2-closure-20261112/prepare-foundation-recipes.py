#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Materialize explicit standard recipes; no runtime or qualification assertions are fabricated."""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--inputs',type=Path,required=True);a=p.parse_args()
r=Path(__file__).resolve().parent;recipes={}
full=json.loads((a.inputs/'scope.json').read_text())
keys=['M01-F01','M01-F02','M01-F03','M02-F01','M02-F02','M02-F03','M02-F04','M03-F01','M03-F02','M03-F03','M04-F01','M04-F02','M06-F02','M06-F03','M06-F04']
scope={'features':{k:v for k,v in full['features'].items() if k in keys or v['disposition']=='deferred'},'dependency_choices':{}}
def add(fid,count,controller,entry):
 for i in range(1,count+1):
  cid=fid+f'-C{i:02d}'
  recipes[cid]={'prepare':[],'resources':[],**{phase:[{'argv':['python3',str(r/controller),phase,'--attempt','{attempt}','--case',cid,'--entrypoint','{repo}/'+entry]}] for phase in ['run','observe','cleanup']}}
for fid in keys[:3]:add(fid,1,'m01-controller.py','adapter/modules/m01-platform-contract/common/discovery/platform-smoke.py')
add('M02-F01',1,'dev-case-controller-r2.py','adapter/modules/m02-build-packaging/common/images/dev-validate.py')
add('M02-F02',1,'wheel-case-controller.py','container/platforms/build-wheels.sh')
add('M02-F03',1,'images-case-controller.py','adapter/modules/m02-build-packaging/common/images/dev-validate.py')
add('M02-F04',1,'offline-case-controller.py','container/platforms/audit.py')
for fid,count,entry in [('M03-F01',1,'run-case.py'),('M03-F02',3,'run-case.py'),('M03-F03',1,'rank-smoke.py')]:add(fid,count,'engine-case-controller-r2.py','adapter/modules/m03-engine-integration/common/engine/'+entry)
for fid,count in [('M04-F01',3),('M04-F02',1)]:add(fid,count,'memory-case-controller.py','adapter/modules/m04-device-memory/common/memory/run.sh')
for fid in ['M06-F02','M06-F03','M06-F04']:add(fid,1,'native-case-controller.py','adapter/modules/m06-kvbm-collectives/common/cache/native-run.py')
for name,data in [('foundation-scope.json',scope),('foundation-recipes.json',recipes)]:
 p=a.inputs/name
 with p.open('x') as f:f.write(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'required_features':len(keys),'case_recipes':len(recipes)}))
