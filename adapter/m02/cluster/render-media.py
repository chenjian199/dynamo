#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reuse M07's real-device media transport and bounded video regression tests."""

import argparse
import base64
import json
from pathlib import Path

from render import NODES, pod_spec

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=NODES, required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
args = p.parse_args()
root = Path(__file__).resolve().parents[3]
meta = {"namespace": args.namespace, "labels": {"campaign": args.namespace}}
runner = """import asyncio, importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path
import pytest, torch
spec = importlib.util.spec_from_file_location('media_checks','/media-tests/test_multimodal.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
completed = []
async def checks():
    for transport in (('local',) if os.environ['DYN_PLATFORM']=='ascend' else ('local','nixl_read','nixl_write')):
        for dtype in (torch.float32,torch.float16,torch.bfloat16):
            with tempfile.TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as patch:
                await module.test_device_embedding_roundtrip(transport,dtype,Path(directory),patch)
                completed.append(str(dtype)+'/'+transport)
    with tempfile.TemporaryDirectory() as directory:
        await module.test_cpu_video_decode(Path(directory))
        completed.append('cpu_video_decode')
    if os.environ['DYN_PLATFORM']!='ascend':
        await module.test_write_late_submission_after_receiver_timeout()
        completed.append('late_write_quarantine')
asyncio.run(checks())
assert len(completed)==(4 if os.environ['DYN_PLATFORM']=='ascend' else 11)
subprocess.run([sys.executable, '/media-tests/video-check.py', '--video', '/tmp/long.webm', '--generate'], check=True)
subprocess.run([sys.executable, '/media-tests/video-check.py', '--video', '/tmp/long.webm', '--output', '/tmp/video.json'], check=True)
print('REGRESSION_RESULT='+json.dumps({'passed': True, 'completed':completed, 'video': json.loads(Path('/tmp/video.json').read_text()), 'qualification': 'device embedding transport and CPU video; not model inference'}))
"""
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {**meta, "name": "regression-media-tests"},
    "data": {
        name: (root / "adapter/m07" / name).read_text()
        for name in ("test_multimodal.py", "video-check.py")
    },
    "binaryData": {
        "red.webm": base64.b64encode(
            (root / "adapter/m07/red.webm").read_bytes()
        ).decode()
    },
}
cm["data"]["run.py"] = runner
spec = pod_spec(
    args.platform, NODES[args.platform][0], ["python", "/media-tests/run.py"], devices=1
)
spec["volumes"].append(
    {"name": "media-tests", "configMap": {"name": cm["metadata"]["name"]}}
)
spec["containers"][0]["volumeMounts"].append(
    {"name": "media-tests", "mountPath": "/media-tests", "readOnly": True}
)
spec["containers"][0]["env"].append(
    {
        "name": "UCX_TLS",
        "value": "tcp,sm,self,cuda_copy,cuda_ipc"
        if args.platform == "cuda"
        else "tcp,sm,self",
    }
)
job = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {**meta, "name": "media-primitives"},
    "spec": {
        "backoffLimit": 0,
        "activeDeadlineSeconds": 900,
        "template": {"metadata": {"labels": meta["labels"]}, "spec": spec},
    },
}
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [cm, job]}, indent=2))
