# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect completed final-image soak evidence on the execution node without changing raw files."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

root = Path('/data/dynamo-upgrade-1.6.2-20261111')
source = root / 'candidate-1aabe/pd-soak-final-r1'
soak = json.loads((source / 'results/soak.json').read_text())
assert soak['status'] in ['passed', 'failed'], 'Soak is still running'
archive_path = root / 'pd-soak-final-55.tar.gz'
manifest = {}
with tarfile.open(archive_path, 'x:gz') as archive:
    for path in sorted(source.rglob('*')):
        if not path.is_file():
            continue
        name = str(path.relative_to(source))
        data = path.read_bytes()
        manifest[name] = hashlib.sha256(data).hexdigest()
        entry = tarfile.TarInfo(name)
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    data = (json.dumps(manifest, indent=2) + '\n').encode()
    entry = tarfile.TarInfo('RAW_SHA256.json')
    entry.size = len(data)
    archive.addfile(entry, io.BytesIO(data))
with archive_path.open('rb') as stream:
    checksum = hashlib.sha256()
    for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
        checksum.update(chunk)
    digest = checksum.hexdigest()
print(json.dumps({'archive': str(archive_path), 'sha256': digest,
                  'bytes': archive_path.stat().st_size, 'files': len(manifest)}))
