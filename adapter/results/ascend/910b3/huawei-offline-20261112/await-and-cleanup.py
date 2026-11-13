#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,subprocess,time
from pathlib import Path
root=Path('/data/dynamo-huawei-backup-20261112')
end=time.monotonic()+900
while time.monotonic()<end:
 data=json.loads((root/'backup-status.json').read_text())
 if data['status']=='failed':raise RuntimeError(data)
 if data['status']=='verified':
  subprocess.run(['python3',str(root/'cleanup-huawei52.py')],check=True)
  break
 time.sleep(10)
else:raise TimeoutError('Archive verification did not complete; no cleanup attempted')
