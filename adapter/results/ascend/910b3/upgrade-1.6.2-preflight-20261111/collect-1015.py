# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot bounded owned evidence; exclude images, models and source trees."""
import tarfile,json,hashlib,io
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');out=r/'diagnostics-1015.tar.gz';paths=[]
for relative in ['candidate-5cbe/advanced-r4','candidate-5cbe/tools-unit-r3','candidate-49ef/kvbm-r3','candidate-49ef/pd-unequal-r3','candidate-49ef/interleaved-npu-probe-r2','baseline-h/engine-smoke-r1','native-small-chat-control-r2','candidate-5cbe/functional-r2','kubernetes-preflight']:
 paths.extend(p for p in (r/relative).rglob('*') if p.is_file())
for pattern in ['candidate-5cbe/tools-build-r*.json','candidate-5cbe/tools-build-r*.log','baseline-h/runtime-r2*.json','baseline-h/runtime-r2.log','distribution/*json','distribution/*sha256','build-old-heyi.log','candidate-49ef/build-launch.json','candidate-49ef/images.json']:
 paths.extend(r.glob(pattern))
manifest={}
with tarfile.open(out,'w:gz') as t:
 for p in sorted(set(paths)):
  if not p.is_file():continue
  data=p.read_bytes();relative=str(p.relative_to(r));manifest[relative]=hashlib.sha256(data).hexdigest();info=tarfile.TarInfo(relative);info.size=len(data);t.addfile(info,io.BytesIO(data))
 data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('RAW_SHA256.json');info.size=len(data);t.addfile(info,io.BytesIO(data))
print(json.dumps({'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'files':len(manifest),'bytes':out.stat().st_size}))
