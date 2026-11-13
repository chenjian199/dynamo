# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,subprocess,time
from pathlib import Path
root=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112')
inv=root/'images-64815280cf-r2/inventory.json'
deadline=time.monotonic()+9000
while not inv.exists():
 if time.monotonic()>deadline:raise TimeoutError('Runtime image inventory unavailable')
 time.sleep(15)
images=json.loads(inv.read_text())
image=images['heyi/dynamo:1.6.2-ascend0.30-64815280cf-runtime']['repo_digests'][0]
manifest=root/'comparison-r2/new_heyi.json'
x=json.loads(manifest.read_text())
(root/'comparison-r2/new_heyi-before-pinning.json').write_text(manifest.read_text())
for component in x['spec']['components']:component['podTemplate']['spec']['containers'][0]['image']=image
manifest.write_text(json.dumps(x,indent=2)+'\n')
subprocess.run(['python3',str(root/'comparison-r2/run-new.py')],check=True,timeout=3600)
