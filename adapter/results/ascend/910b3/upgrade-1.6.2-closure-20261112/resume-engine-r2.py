# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parent
repo=Path('/home/metax/CJ_workspace/dynamo/.worktrees/source-1.6.2-ascend-20261111/.worktrees/dynamo-1.6.2-ascend-20261111')
campaign=repo/'adapter/results/ascend/910b3/upgrade162-engine-9ee6-20261112'
r={'started':time.time(),'status':'running','reason':'Compile requires explicit recipes even for verified inherited dependencies; no sealed plan or runtime attempt was changed.'}
try:
 for i,cmd in enumerate([['compile','--recipes',str(root/'campaign-inputs-9ee6/engine-recipes-r2.json')],['seal-plan'],['run']]):
  with (root/f'engine-resume-r2-{i}.log').open('x') as f:subprocess.run(['python3','-m','adapter.workflow',*cmd,'--campaign',str(campaign)],cwd=repo,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=21600)
 state=json.loads((campaign/'state.json').read_text());r['features']={k:{x:v.get(x) for x in ('test','qualification')} for k,v in state['features'].items()}
 r['status']='verified' if all(v['qualification']=='verified' for v in r['features'].values()) else 'not_qualified'
except Exception as e:r.update(status='failed',error=repr(e))
finally:
 r['finished']=time.time();(root/'engine-resume-r2.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2),flush=True)
