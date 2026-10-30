#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Capture package/driver/model-config identity from one live campaign worker."""
import argparse
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--name", default="m09-small")
p.add_argument("--namespace", default="dynamo-m08-1029")
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
k = ["kubectl", "-n", a.namespace]
pods = json.loads(
    subprocess.check_output(
        k
        + [
            "get",
            "pods",
            "-l",
            f"nvidia.com/dynamo-graph-deployment-name={a.name},nvidia.com/dynamo-component=VllmDecodeWorker",
            "-o",
            "json",
        ]
    )
)["items"]
pod = next(
    p
    for p in pods
    if not p["metadata"].get("deletionTimestamp")
    and any(c.get("ready") for c in p["status"].get("containerStatuses", []))
)
code = """
import hashlib, importlib.metadata as m, json, os, subprocess
from pathlib import Path
platform=os.environ['DYN_PLATFORM']
packages={name:m.version(name) for name in ['vllm','torch','ai-dynamo','ai-dynamo-runtime']}
if platform=='ascend':
 packages.update({n:m.version(n) for n in ['vllm-ascend','torch-npu']})
 def version(path,key):
  return next(line.split('=',1)[1].strip() for line in Path(path).read_text().splitlines() if line.startswith(key+'='))
 driver=version('/usr/local/Ascend/driver/version.info','Version')
 sdk=version('/usr/local/Ascend/ascend-toolkit/latest/arm64-linux/ascend_toolkit_install.info','version')
else:
 driver=subprocess.check_output(['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader'],text=True).splitlines()[0]
 sdk=packages['torch'].split('+')[-1]
model='/models/Qwen3-0.6B' if Path('/models/Qwen3-0.6B/config.json').is_file() else '/models/current'
print(json.dumps(dict(platform=platform,packages=packages,driver=driver,sdk=sdk,model=model,model_config_sha256=hashlib.sha256((Path(model)/'config.json').read_bytes()).hexdigest())))
"""
record = json.loads(
    subprocess.check_output(
        k
        + [
            "exec",
            pod["metadata"]["name"],
            "--",
            "/opt/dynamo/venv/bin/python",
            "-c",
            code,
        ],
        timeout=60,
    )
)
record.update(
    node=pod["spec"]["nodeName"],
    pod=pod["metadata"]["name"],
    image_id=pod["status"]["containerStatuses"][0]["imageID"],
)
a.output.write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record))
