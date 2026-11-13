# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect only named completed acceptance directories, retaining original bytes."""
import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('node', choices=['52', '55'])
a = p.parse_args()
root = Path('/data/dynamo-upgrade-1.6.2-20261111')
names = {
    '52': ['candidate-1aabe/tools-unit-r2', 'candidate-1aabe/dev-editable-r1',
           'operator-1aabe', 'hardware-final-r1', 'advanced-final-r1',
           'qwen32-final-r1'],
    '55': ['candidate-1aabe/hardware-final-r1/results',
           'candidate-1aabe/advanced-final-r1/results'],
}[a.node]
paths = [p for name in names for p in (root / name).rglob('*')
         if p.is_file() and 'source' not in p.relative_to(root).parts
         and p.suffix in {'.json', '.log', '.txt', '.xml', '.tsv', '.csv', '.yaml', '.sh', '.py'}]
if a.node == '52':
    paths += [root / 'candidate-1aabe/images.json']
out = root / ('final-acceptance-' + a.node + '-1140.tar.gz')
manifest = {}
with tarfile.open(out, 'x:gz') as archive:
    for path in sorted(set(paths)):
        data = path.read_bytes()
        name = str(path.relative_to(root))
        manifest[name] = hashlib.sha256(data).hexdigest()
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    data = (json.dumps(manifest, indent=2) + '\n').encode()
    info = tarfile.TarInfo('RAW_SHA256.json')
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))
print(json.dumps({'node': a.node, 'sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
                  'files': len(manifest), 'bytes': out.stat().st_size}))
