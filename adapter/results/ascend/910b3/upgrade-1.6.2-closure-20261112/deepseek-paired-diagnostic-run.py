# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,subprocess,sys,time
from pathlib import Path
out=Path('/evidence'); result={'started':time.time(),'purpose':'Diagnostic only; validate batch invariance before formal advanced cases','arms':{}}
try:
 for arm in ['native','dynamo']:
  cmd=[sys.executable,'/controller/deepseek-paired-diagnostic-probe.py','--arm',arm,'--output','/evidence/'+arm,'--spec','/controller/deepseek-paired-diagnostic-spec.json']
  with (out/(arm+'.log')).open('x') as f:p=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=2100)
  result['arms'][arm]={'command':cmd,'returncode':p.returncode}
  if p.returncode:raise RuntimeError(arm+' failed; raw results retained')
 d=json.loads((out/'dynamo/result.json').read_text());n=json.loads((out/'native/result.json').read_text())
 result['native_tokens']=[x['choices'][0]['token_ids'] for x in n['responses']]
 result['dynamo_tokens']=[x['nvext']['completion_token_ids'] for x in d['responses']]
 result['paired_parity']=result['native_tokens']==result['dynamo_tokens']
 assert result['paired_parity']
 result['passed']=True
except Exception as e:result.update(passed=False,error=repr(e));raise
finally:
 result['finished']=time.time();(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
