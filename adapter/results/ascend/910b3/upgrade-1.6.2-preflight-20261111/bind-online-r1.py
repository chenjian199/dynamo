# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import yaml,json
from pathlib import Path
p=Path('/offline/online-r1.yaml');objects=list(yaml.safe_load_all(p.read_text()))
for o in objects:
 if o['kind']=='DynamoGraphDeployment':
  for c in o['spec']['components']:c['runtimeVersionOverride']='1.6.2'
p.write_text(yaml.safe_dump_all(objects,sort_keys=False))
