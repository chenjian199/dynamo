# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import hashlib,json,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111/distribution');p=r/'ascend-0.30-native.tar';deadline=time.monotonic()+1200
while not p.exists():
 if time.monotonic()>deadline:raise TimeoutError('Owned native image export')
 time.sleep(5)
h=hashlib.sha256()
with p.open('rb') as f:
 for data in iter(lambda:f.read(16*1024*1024),b''):h.update(data)
(r/'ascend-0.30-native.sha256').write_text(h.hexdigest()+'  '+p.name+'\n')
(r/'native-export-result.json').write_text(json.dumps({'sha256':h.hexdigest(),'bytes':p.stat().st_size,'image':'quay.io/ascend/vllm-ascend:v0.30.0','finished':time.time()},indent=2))
