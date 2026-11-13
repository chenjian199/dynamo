#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Record actual TransferEngine calls; forward unchanged arguments and return values."""
import importlib.util
import json
import os
import sys
from pathlib import Path

entry=Path(os.environ.get('CLOSURE_TRANSPORT_ENTRY') or sys.argv[1]).resolve()
os.environ['CLOSURE_TRANSPORT_ENTRY']=str(entry)
spec=importlib.util.spec_from_file_location('closure_transport_probe',entry)
probe=importlib.util.module_from_spec(spec);sys.modules[spec.name]=probe;spec.loader.exec_module(probe)
original_worker=probe.worker

def traced_worker(rank,pipe,args):
    os.environ['ASCEND_RT_VISIBLE_DEVICES']=args.devices.split(',')[rank]
    from dynamo.common.platform.ascend_mooncake import prepare_mooncake_libraries
    prepare_mooncake_libraries()
    import mooncake.engine as native
    original_engine=native.TransferEngine
    events=[]
    path=args.output/f'rank-{rank}.transport-calls.json'
    def emit(name,argv,result):
        events.append({'method':name,'arguments':list(argv),'return':result})
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(events,indent=2)+'\n');tmp.replace(path)
    class ObservedEngine:
        def __init__(self,*args,**kw):self.engine=original_engine(*args,**kw)
        def __getattr__(self,name):
            value=getattr(self.engine,name)
            if name not in ('initialize','register_memory','unregister_memory','batch_transfer_sync_read'):return value
            def invoke(*args,**kw):
                assert not kw, 'Record keyword arguments explicitly before supporting them'
                result=value(*args);emit(name,args,result);return result
            return invoke
    native.TransferEngine=ObservedEngine
    try:original_worker(rank,pipe,args)
    finally:native.TransferEngine=original_engine

if __name__=='__main__':
    probe.worker=traced_worker
    sys.argv=[str(entry),*sys.argv[2:]]
    probe.main()
