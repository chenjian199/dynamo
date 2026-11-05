#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise relay initialization while Rust metrics tasks call back into Python.

Run this process under an external timeout: a native GIL deadlock cannot be
interrupted by Python's asyncio timeout. No model or GPU is required.
"""
import asyncio
import os
import threading
import time
import urllib.request

os.environ.update(
    DYN_SYSTEM_PORT="19980",
    DYN_FILE_KV="/tmp/m10-fpm-probe",
    DYN_REQUEST_PLANE="tcp",
    DYN_EVENT_PLANE="zmq",
    DYN_RUNTIME_NUM_WORKER_THREADS="2",
)
from dynamo.runtime import DistributedRuntime
from dynamo.llm import FpmEventRelay


async def main():
    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "file", "tcp", event_plane="zmq"
    )
    endpoint = runtime.endpoint("m10fpm.worker.generate")
    await endpoint.register_endpoint_instance()
    callback_count = [0]

    def metrics():
        callback_count[0] += 1
        time.sleep(0.002)
        return "# callback\n"

    endpoint.metrics.register_prometheus_expfmt_callback(metrics)
    stop = threading.Event()

    def scrape():
        while not stop.is_set():
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:19980/metrics", timeout=2
                ).read()
            except Exception:
                pass

    threads = [threading.Thread(target=scrape, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    relays = []
    try:
        await asyncio.sleep(0.1)
        for i in range(20):
            print("START", i, flush=True)
            relays.append(
                FpmEventRelay(endpoint=endpoint, zmq_endpoint="tcp://127.0.0.1:19981")
            )
            await asyncio.sleep(0.01)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=3)
        for relay in relays:
            relay.shutdown()
        await asyncio.sleep(0.5)
        runtime.shutdown()
        await asyncio.sleep(0.5)
    assert callback_count[0] > 0, "Metrics callbacks were not exercised"
    print("FPM_CONCURRENCY_PASS", callback_count[0], flush=True)


asyncio.run(main())
