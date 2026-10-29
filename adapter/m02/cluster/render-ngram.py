#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Paired Qwen3-0.6B Dynamo baseline/ngram speculative decoding checks."""

import argparse
import json

from render import NODES, pod_spec, serving

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=NODES, required=True)
p.add_argument("--namespace", default="dynamo-regression-1028")
args = p.parse_args()
path = (
    "/ssd/models/dynamo-regression/Qwen3-0.6B"
    if args.platform == "cuda"
    else "/data/weight/Qwen3-0.6B"
)
base = serving(
    args.platform, args.namespace, {NODES[args.platform][0]: path}, prefix="ngram-base"
)
speculative = serving(
    args.platform, args.namespace, {NODES[args.platform][0]: path}, prefix="ngram"
)
items = base["items"] + [
    x for x in speculative["items"] if x["metadata"]["name"] != "regression-etcd"
]
for item in items:
    if item["metadata"]["name"] == "ngram-worker-0":
        item["spec"]["template"]["spec"]["containers"][0]["command"] += [
            "--speculative-config",
            json.dumps(
                {
                    "method": "ngram",
                    "num_speculative_tokens": 3,
                    "prompt_lookup_min": 2,
                    "prompt_lookup_max": 4,
                }
            ),
        ]
meta = {"namespace": args.namespace, "labels": {"campaign": args.namespace}}
items.append(
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {**meta, "name": "ngram-worker-api"},
        "spec": {
            "selector": {"app": "ngram-worker-0"},
            "ports": [{"port": 8081, "targetPort": 8081}],
        },
    }
)
script = """import json,time,urllib.request,urllib.error
from prometheus_client.parser import text_string_to_metric_families
def call(url,p=None):
 req=urllib.request.Request(url,data=None if p is None else json.dumps(p).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=120) as r:return json.load(r)
for prefix in ['ngram-base','ngram']:
 deadline=time.monotonic()+900
 while True:
  try:
   if call('http://'+prefix+'-frontend:8000/v1/models/'+prefix+'-model/ready').get('ready'):break
  except (urllib.error.URLError,TimeoutError):pass
  if time.monotonic()>deadline:raise TimeoutError(prefix)
  time.sleep(5)
report={'passed':False,'responses':{}}
try:
 for prefix in ['ngram-base','ngram']:
  payload={'model':prefix+'-model','prompt':'Continue the repeating sequence without explanation: '+'one two three four '*60,'max_tokens':128,'temperature':0,'seed':42,'ignore_eos':True}
  results=[call('http://'+prefix+'-frontend:8000/v1/completions',payload) for _ in range(3)]
  assert all(r['usage']['completion_tokens']==128 for r in results)
  report['responses'][prefix]=results
 texts=[r['choices'][0]['text'] for rows in report['responses'].values() for r in rows]
 assert len(set(texts))==1,'baseline/spec output differs'
 time.sleep(6)
 with urllib.request.urlopen('http://ngram-worker-api:8081/metrics',timeout=10) as r:metrics=r.read().decode()
 rows={s.name:s.value for f in text_string_to_metric_families(metrics) for s in f.samples if 'spec_decode' in s.name}
 report['spec_metrics']=rows
 assert any('num_drafts' in k and v>0 for k,v in rows.items()),rows
 assert any('accepted_tokens_total' in k and v>0 for k,v in rows.items()),rows
 report['passed']=True
except Exception as e:
 report['error']=repr(e);raise
finally:print('REGRESSION_RESULT='+json.dumps(report),flush=True)
"""
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {**meta, "name": "ngram-client-scripts"},
    "data": {"run.py": script},
}
s = pod_spec(
    args.platform, NODES[args.platform][0], ["python", "/client/run.py"], devices=0
)
s["containers"][0]["env"].append({"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"})
s["volumes"].append({"name": "client", "configMap": {"name": cm["metadata"]["name"]}})
s["containers"][0]["volumeMounts"].append(
    {"name": "client", "mountPath": "/client", "readOnly": True}
)
job = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {**meta, "name": "ngram-client"},
    "spec": {
        "backoffLimit": 0,
        "activeDeadlineSeconds": 1500,
        "template": {"metadata": {"labels": meta["labels"]}, "spec": s},
    },
}
items += [cm, job]
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=2))
