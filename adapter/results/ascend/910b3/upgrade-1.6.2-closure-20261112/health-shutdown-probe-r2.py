# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CPU-only diagnostic: canary callbacks must stop when runtime shutdown starts."""
import asyncio
import json
import os
import time

os.environ.update(DYN_HEALTH_CHECK_ENABLED='true', DYN_RUNTIME_NUM_WORKER_THREADS='2', DYN_PLATFORM='cpu', DYN_SYSTEM_PORT='0', DYN_CANARY_WAIT_TIME='1',
                  DYN_HEALTH_CHECK_REQUEST_TIMEOUT='2', DYN_GRACEFUL_SHUTDOWN_GRACE_PERIOD_SECS='0')
from dynamo.runtime import DistributedRuntime

async def main():
    runtime = DistributedRuntime(asyncio.get_running_loop(), 'mem', 'tcp', event_plane='zmq')
    endpoint = runtime.endpoint('shutdown-probe.backend.generate')
    calls = []
    async def handler(request, context):
        calls.append(time.monotonic())
        yield {'token_ids': [1]}
    task = asyncio.ensure_future(endpoint.serve_endpoint(handler, health_check_payload={'text':'health'}))
    try:
        deadline = time.monotonic() + 6
        while not calls and time.monotonic() < deadline:
            await asyncio.sleep(.05)
        assert calls, 'canary callback not observed before shutdown'
        runtime.shutdown()
        await asyncio.wait_for(task, 5)
        # Allow any callback already queued at shutdown to finish before counting.
        await asyncio.sleep(.25)
        before = len(calls)
        await asyncio.sleep(3)
        result = {'callbacks_before_shutdown_quiescence': before,
                  'callbacks_after_shutdown_quiescence': len(calls)-before,
                  'callbacks': calls, 'scope': 'CPU runtime lifecycle, no accelerator or model'}
        print(json.dumps(result), flush=True)
        assert len(calls) == before, 'health callbacks continued after endpoint shutdown'
    finally:
        runtime.shutdown()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

asyncio.run(main())
