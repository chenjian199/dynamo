# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Freeze tests-only harness; implementation is imported from installed wheels."""
import hashlib
import json
from pathlib import Path
import shutil
root=Path('/data/dynamo-upgrade-1.6.2-20261111')
source=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111')
out=root/'candidate-5cbe/full-harness'
assert not (out/'SOURCE.json').exists()
manifest=json.loads((source/'SOURCE.json').read_text())
assert manifest['commit']=='5cbe424879e88f3da56e3f99054f8bcfde49165c'
selected=[]
for path in source.rglob('*'):
    if not path.is_file():continue
    name=str(path.relative_to(source))
    if (name.startswith('adapter/modules/') and '/common/' in name) or name.startswith('adapter/platforms/') or name.startswith('tests/utils/') or name in ('tests/__init__.py','adapter/__init__.py'):
        if name not in manifest['files']:continue
        assert hashlib.sha256(path.read_bytes()).hexdigest()==manifest['files'][name]
        dest=out/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest);selected.append(name)
reg=source/'adapter/modules/m10-validation/common/regression'
for name in ('check-installed.py','run-advanced.sh'):shutil.copy2(reg/name,out/name)
shutil.copy2(reg/'run-engine.py',out/'adapter/modules/m03-engine-integration/common/engine/run-case.py')
shutil.copytree(root/'cache/test-deps-upgrade',out/'test-deps',dirs_exist_ok=True)
(out/'pytest.ini').write_text('[pytest]\nasyncio_mode = auto\nmarkers =\n    post_merge: post merge\n    integration: integration\n    gpu_1: one device\n')
files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
(out/'SOURCE.json').write_text(json.dumps({'commit':manifest['commit'],'kind':'tests-only','files':files},indent=2)+'\n')
print(json.dumps({'files':len(files),'source_commit':manifest['commit']}))
