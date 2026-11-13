# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import yaml,json
from pathlib import Path
p=Path('/offline/online-r1.yaml');objects=list(yaml.safe_load_all(p.read_text().replace('m10-offline-final','m10-offline-hysteresis')))
for o in objects:
 if o['kind']=='DynamoGraphDeployment':
  for c in o['spec']['components']:c['runtimeVersionOverride']='1.6.2'
for o in objects:
 if o['kind']=='ConfigMap' and 'planner_config.json' in o.get('data',{}):
  config=json.loads(o['data']['planner_config.json'])
  config.update(enable_load_scaling=True, throughput_adjustment_interval_seconds=180, itl_ms=75, load_scaling_down_sensitivity=95)
  o['data']['planner_config.json']=json.dumps(config)
p.write_text(yaml.safe_dump_all(objects,sort_keys=False))
