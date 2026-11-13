# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Import the verified four-role export into owned cluster image storage and audit config IDs."""
import hashlib
import json
import subprocess
import time
from pathlib import Path

r = Path('/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate')
report = {'started': time.time(), 'passed': False, 'cold_empty_store': False}
try:
    inventory = json.loads((r / 'image-export.json').read_text())
    assert json.loads((r / 'image-archive-verification.json').read_text())['passed']
    archive = r / inventory['archive']
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    report['archive_sha256'] = digest.hexdigest()
    assert report['archive_sha256'] == inventory['sha256']
    with (r / 'import-52.log').open('x') as log:
        result = subprocess.run(['k3s', 'ctr', '-n', 'k8s.io', 'images', 'import', '--platform', 'linux/arm64', str(archive)], stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    result.check_returncode()
    identities = []
    for image in inventory['images']:
        actual = json.loads(subprocess.check_output(['k3s', 'crictl', 'inspecti', image['reference']], text=True))
        identities.append({'reference': image['reference'], 'expected': image['id'], 'actual': actual['status']['id']})
        assert actual['status']['id'] == image['id'], identities[-1]
    report.update(passed=True, identities=identities)
except Exception as error:
    report['error'] = repr(error)
finally:
    report['finished'] = time.time()
    (r / 'import-52.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
