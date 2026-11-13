# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,time
from pathlib import Path
out=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/images-64815280cf-r2')
public=Path('/data/dynamo-upgrade-1.6.2-20261111/distribution/closure-64815280cf')
public.mkdir(parents=True,exist_ok=True)
deadline=time.monotonic()+7200
while not (out/'inventory.json').exists():
 if time.monotonic()>deadline:raise TimeoutError('Image export not ready')
 time.sleep(10)
record=json.loads((out/'export/image-export.json').read_text())
record['inventory']=json.loads((out/'inventory.json').read_text())
record['archive']='closure-64815280cf-images.tar.gz'
link=public/record['archive'];assert not link.exists();link.symlink_to(out/'export/images.tar.gz')
manifest=public/'closure-64815280cf-images.json';assert not manifest.exists()
manifest.write_text(json.dumps(record,indent=2)+'\n')
print(manifest,flush=True)
