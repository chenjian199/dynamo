#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Invoke the canonical cross-Pod runner with transparent native call observation."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

original=subprocess.run
records=[]

def observed(argv,*args,**kwargs):
    effective=list(argv)
    target='/tmp/m08-rdma-transfer.py'
    if target in effective:
        index=effective.index(target)
        assert effective[index-1]=='python'
        effective.insert(index,'/observer/observe_native_transport.py')
        records.append({'original':list(argv),'executed':effective})
    return original(effective,*args,**kwargs)

subprocess.run=observed
script=Path(sys.argv[1]);sys.argv=sys.argv[1:]
try:
    runpy.run_path(str(script),run_name='__main__')
finally:
    print('PROBE_WRAPPER_AUDIT='+json.dumps({'runner_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'commands':records}),flush=True)
