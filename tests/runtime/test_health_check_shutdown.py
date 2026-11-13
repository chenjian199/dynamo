# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Active canaries must not call into Python after endpoint shutdown."""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = [pytest.mark.pre_merge, pytest.mark.integration, pytest.mark.gpu_0]

CHILD = textwrap.dedent(
    """
    import asyncio
    import json
    import sys
    from dynamo.runtime import DistributedRuntime

    if sys.argv[1] == 'uvloop':
        import uvloop
        uvloop.install()

    async def main():
        runtime = DistributedRuntime(asyncio.get_running_loop(), 'mem', 'tcp', event_plane='zmq')
        endpoint = runtime.endpoint('shutdown-probe.backend.generate')
        called = asyncio.Event()
        calls = 0

        async def handler(request, context):
            nonlocal calls
            calls += 1
            called.set()
            yield {'token_ids': [1]}

        task = asyncio.ensure_future(endpoint.serve_endpoint(
            handler, health_check_payload={'text': 'health'}))
        try:
            await asyncio.wait_for(called.wait(), 6)
            runtime.shutdown()
            await asyncio.wait_for(task, 5)
            # Finish an already-queued callback, then observe multiple canary periods.
            await asyncio.sleep(.25)
            before = calls
            await asyncio.sleep(3)
            print('HEALTH_SHUTDOWN=' + json.dumps({
                'before': before, 'after': calls - before}), flush=True)
        finally:
            runtime.shutdown()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())
    """
)


@pytest.mark.parametrize("event_loop", ["asyncio", "uvloop"])
def test_health_callbacks_stop_before_interpreter_exit(event_loop):
    if event_loop == "uvloop" and importlib.util.find_spec("uvloop") is None:
        pytest.skip("uvloop is not installed")
    env = dict(os.environ)
    # An isolated interpreter is necessary to also verify normal CPython teardown.
    env.update(
        DYN_PLATFORM="cpu",
        DYN_SYSTEM_PORT="0",
        DYN_HEALTH_CHECK_ENABLED="true",
        DYN_CANARY_WAIT_TIME="1",
        DYN_HEALTH_CHECK_REQUEST_TIMEOUT="2",
        DYN_RUNTIME_NUM_WORKER_THREADS="2",
        DYN_RUNTIME_MAX_BLOCKING_THREADS="2",
    )
    result = subprocess.run(
        [sys.executable, "-c", CHILD, event_loop],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(
        next(
            line.removeprefix("HEALTH_SHUTDOWN=")
            for line in result.stdout.splitlines()
            if line.startswith("HEALTH_SHUTDOWN=")
        )
    )
    assert report["before"] > 0
    assert report["after"] == 0, result.stdout + result.stderr
