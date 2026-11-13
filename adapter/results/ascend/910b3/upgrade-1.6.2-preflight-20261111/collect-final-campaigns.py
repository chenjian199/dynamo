# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot named final/diagnostic campaigns as byte-hashed evidence, without source or model trees."""
import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--name', required=True)
a = p.parse_args()
assert a.name.isalnum(), 'Archive suffix must be alphanumeric'
r = Path('/data/dynamo-upgrade-1.6.2-20261111')
bundle = Path('/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate')
names = ['profiling-r1', 'profiling-r3', 'profiling-r4', 'profiling-r5', 'dgdr-r2', 'dynamo-deepseek-api-final-r1',
         'dynamo-deepseek-api-final-r2', 'cluster-services/pd-cross-final-r1', 'hybrid-pd-r1',
         'cluster-services/grove-final-r1', 'candidate-1aabe/pd-split-final-r1', 'pd-split-final-r1', 'pp2-final-r1', 'pd-soak-final-r1',
         'cleanup-final']
files = [(path, str(path.relative_to(r))) for name in names for path in (r / name).rglob('*')
         if path.is_file() and '__pycache__' not in path.parts]
files += [(path, 'offline-candidate/' + path.name) for path in bundle.glob('*')
          if path.is_file() and path.suffix in ['.json', '.log', '.md']]
files += [(path, 'offline-candidate/dependencies/' + path.name) for path in (bundle / 'dependencies').glob('*.json')]
files += [(path, path.name) for path in r.glob('*final*.log')]
files += [(r / 'queue-final-pd-status.json', 'queue-final-pd-status.json')]
out = r / ('final-campaigns-' + a.name + '.tar.gz')
manifest = {}
with tarfile.open(out, 'x:gz') as archive:
    for path, relative in sorted(set(files)):
        data = path.read_bytes()
        manifest[relative] = hashlib.sha256(data).hexdigest()
        entry = tarfile.TarInfo(relative)
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    data = (json.dumps(manifest, indent=2) + '\n').encode()
    entry = tarfile.TarInfo('RAW_SHA256.json')
    entry.size = len(data)
    archive.addfile(entry, io.BytesIO(data))
print(json.dumps({'archive': str(out), 'files': len(manifest), 'bytes': out.stat().st_size,
                  'sha256': hashlib.sha256(out.read_bytes()).hexdigest()}))
