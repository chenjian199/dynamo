#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Record original native API selection and byte submissions around the unmodified probe."""
import functools
import hashlib
import json
from pathlib import Path
import runpy
import sys
from dynamo.common.platform.ascend_mooncake import prepare_mooncake_libraries
prepare_mooncake_libraries()
from mooncake.engine import TransferEngine

records=[]
original_init=TransferEngine.initialize

@functools.wraps(original_init)
def initialize(self,*args,**kwargs):
    value=original_init(self,*args,**kwargs)
    records.append({'method':'initialize','engine':id(self),'args':args,'kwargs':kwargs,'returncode':value})
    return value

TransferEngine.initialize=initialize
for name in ('batch_transfer_sync_read','batch_transfer_sync_write'):
    original=getattr(TransferEngine,name)
    def wrap(method, method_name):
        @functools.wraps(method)
        def call(self,*args,**kwargs):
            value=method(self,*args,**kwargs)
            records.append({'method':method_name,'engine':id(self),'sizes':list(args[3]),'session':args[0],'returncode':value})
            return value
        return call
    setattr(TransferEngine,name,wrap(original,name))
script=Path(sys.argv[1]);sys.argv=sys.argv[1:]
try:
    runpy.run_path(str(script),run_name='__main__')
finally:
    print('NATIVE_TRANSPORT_AUDIT='+json.dumps({'probe_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'calls':records}),flush=True)
