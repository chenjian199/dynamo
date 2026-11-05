#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify a real API request after the offline Planner restart receipt exists."""
import argparse
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--name", required=True)
p.add_argument("--tools-pod", required=True)
p.add_argument("--restart", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
receipt = json.loads((a.restart / "result.json").read_text())
if not receipt["passed"]:
    raise SystemExit("Planner restart did not pass")
code = """
import json, sys, time, urllib.request
body=json.dumps({'model':'m09-model','messages':[{'role':'user','content':'Say hello briefly.'}],'max_tokens':16,'temperature':0,'ignore_eos':True}).encode()
start=time.time()
with urllib.request.urlopen(urllib.request.Request(sys.argv[1], data=body, headers={'Content-Type':'application/json'}),timeout=240) as r:
 d=json.load(r)
 result={'status':r.status,'elapsed_seconds':time.time()-start,'usage':d.get('usage'),'choices':len(d.get('choices',[]))}
 assert r.status==200 and result['choices']>0 and result['usage']['completion_tokens']>0
 print(json.dumps(result))
"""
response = subprocess.check_output(
    [
        "kubectl",
        "-n",
        "dynamo-m08-1029",
        "exec",
        a.tools_pod,
        "--",
        "/opt/dynamo/venv/bin/python",
        "-c",
        code,
        f"http://{a.name}-frontend:8000/v1/chat/completions",
    ],
    timeout=270,
    text=True,
)
result = json.loads(response)
result["planner_uid"] = receipt["after"]["uid"]
result["passed"] = True
a.output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
