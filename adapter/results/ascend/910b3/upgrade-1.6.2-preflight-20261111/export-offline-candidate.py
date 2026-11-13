# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stage the four immutable image roles with the canonical exporter; do not seal a release."""
import json
import os
import subprocess
import time
from pathlib import Path

r = Path('/data/dynamo-upgrade-1.6.2-20261111')
s = Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111')
out = Path('/root/CJ_workspace/dynamo-1.6.2-ascend-0.30.0-1aabe435ad-candidate')
out.mkdir()
build = json.loads((r / 'candidate-1aabe/images.json').read_text())
assert build['source_commit'] == '1aabe435ad56922cf7e12f92f64df2dc39531ccb'
refs = [build[k]['tag'] for k in ['runtime', 'dev', 'tools']]
refs.append('heyi/dynamo-operator:1.6.2-heyi-upgrade-1aabe435ad-arm64')
env = {**os.environ, 'PATH': '/data/dynamo-m10/docker-bin:' + os.environ['PATH'], 'DOCKER_HOST': 'unix:///run/dynamo-m10-docker.sock', 'DOCKER_CONFIG': str(r / 'docker-client-direct')}
record = {'source_commit': build['source_commit'], 'started': time.time(), 'status': 'exporting_unqualified_candidate', 'release_qualified': False, 'path': str(out)}
(out / 'candidate.json').write_text(json.dumps(record, indent=2))
with (out / 'export.log').open('x') as log:
    p = subprocess.run(['python3', str(s / 'adapter/modules/m10-validation/common/offline/export-images.py'), '--output', str(out), *refs], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
record.update(exit_code=p.returncode, finished=time.time(), status='images_exported_pending_validation' if p.returncode == 0 else 'export_failed')
(out / 'candidate.json').write_text(json.dumps(record, indent=2))
print(json.dumps(record), flush=True)
