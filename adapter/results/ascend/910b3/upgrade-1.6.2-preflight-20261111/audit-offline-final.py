# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Audit complete final profiles with byte-identical snapshot aliases for canonical auditor."""
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

r = Path('/data/dynamo-upgrade-1.6.2-20261111/profiling-r3')
script = Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/modules/m10-validation/common/profiling/audit-profile.py')
record = {'started': time.time(), 'passed': False}
try:
    end = time.monotonic() + 3900
    while not (r / 'result.json').exists():
        if time.monotonic() > end:
            raise TimeoutError('Offline grid completion')
        time.sleep(15)
    assert json.loads((r / 'result.json').read_text())['passed']
    aliases = r / 'audit-snapshot-aliases'
    aliases.mkdir()
    mapping = []
    for src in sorted((r / 'snapshots').glob('*-dynamoworkermetadata.json')):
        target = aliases / src.name.replace('-dynamoworkermetadata.json', '.mdc.json')
        shutil.copyfile(src, target)
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        mapping.append({'original': str(src.relative_to(r)), 'alias': str(target.relative_to(r)), 'sha256': digest})
    (r / 'audit-snapshot-map.json').write_text(json.dumps(mapping, indent=2))
    p = subprocess.run(['python3', str(script), '--profile', str(r / 'output'), '--snapshots', str(aliases), '--log', str(r / 'run.log'), '--output', str(r / 'audit')], capture_output=True, text=True, timeout=120)
    (r / 'audit.log').write_text(p.stdout + p.stderr)
    record['passed'] = p.returncode == 0 and json.loads((r / 'audit/execution.json').read_text())['passed']
except Exception as error:
    record['error'] = repr(error)
finally:
    record['finished'] = time.time()
    (r / 'audit-result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
