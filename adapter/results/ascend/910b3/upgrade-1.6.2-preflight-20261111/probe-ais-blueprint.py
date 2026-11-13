# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json,inspect
from pathlib import Path
from aisimulate.generator.enumerate import enumerate_profiling_configs
from aisimulate_core.sdk import perf_database
from dynamo.profiler.utils.platform_profile import require_system_capacity
print(inspect.signature(enumerate_profiling_configs))
require_system_capacity('ascend_910b3',perf_database.get_systems_paths(),65536)
x=enumerate_profiling_configs(model_path='/models/Qwen3-0.6B',system='ascend_910b3',backend='vllm',image='heyi/dynamo:1.6.2-ascend0.30-49ef509336-runtime',isl=128,osl=16,num_gpus_per_node=8,total_gpus=2,k8s_pvc_name=None,k8s_pvc_mount_path='/models',k8s_model_path_in_pvc='Qwen3-0.6B')
a=[c.dgd_config for group in x[:2] for c in group];Path('/offline/ais-blueprints.json').write_text(json.dumps(a,indent=2));print('candidates',len(a));print(json.dumps(a[0],indent=2))
