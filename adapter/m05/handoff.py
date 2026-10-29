#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inject faults in a test-only copy of the prefill handoff, without host network changes."""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from dynamo.runtime import DistributedRuntime


async def run(args):
    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "file", "tcp", event_plane="zmq"
    )
    result = {"case": args.case, "status": "failed"}
    try:
        producer = await runtime.endpoint(args.namespace + ".prefill.generate").client()
        consumer = await runtime.endpoint(args.namespace + ".backend.generate").client()
        await asyncio.wait_for(producer.wait_for_instances(), 10)
        await asyncio.wait_for(consumer.wait_for_instances(), 10)
        request = {
            "model": "m05-model",
            "token_ids": [1000, 1001] * 280,
            "stop_conditions": {"max_tokens": 32},
            "sampling_options": {"temperature": 0},
        }
        stream = await producer.generate(request)
        outputs = [item.data() async for item in stream]
        if len(outputs) != 1 or not outputs[0].get("disaggregated_params"):
            raise RuntimeError(f"Unexpected prefill response: {outputs}")
        handoff = outputs[0]
        params = handoff["disaggregated_params"]["kv_transfer_params"]
        if args.case == "mismatch":
            params["_dynamo_pd_contract"]["block_size"] += 1
        else:
            if "remote_bootstrap_addr" in params:
                params[
                    "remote_bootstrap_addr"
                ] = f"http://127.0.0.1:{args.unreachable_port}"
            else:
                params["remote_port"] = args.unreachable_port
            # A new ID prevents reusing a cached successful handshake.
            params["remote_engine_id"] = str(uuid.uuid4())
        request["prefill_result"] = handoff
        start = time.monotonic()
        responses = []
        try:
            async with asyncio.timeout(90):
                stream = await consumer.generate(request)
                async for item in stream:
                    responses.append(item.data())
        except TimeoutError:
            result["outcome"] = "client_timeout_without_backend_completion"
        except Exception as exc:
            # Fault tests record the expected transport exception, then assert its
            # message rather than treating arbitrary exceptions as success.
            result["outcome"] = "backend_error"
            result["error"] = str(exc)
        else:
            result["outcome"] = "backend_response"
        result.update(elapsed_seconds=time.monotonic() - start, responses=responses)
        encoded = json.dumps(result)
        if args.case == "mismatch":
            if "Incompatible PD peer" not in encoded:
                raise RuntimeError(f"Mismatch not rejected: {encoded}")
            result["status"] = "passed"
        elif any(item.get("finish_reason") == "error" for item in responses):
            result[
                "status"
            ] = "passed"  # Runner verifies drain and recovery separately.
        else:
            raise RuntimeError(
                f"Unreachable peer did not return an explicit backend error: {encoded}"
            )
    finally:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        runtime.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--case", choices=["mismatch", "unreachable"], required=True)
    parser.add_argument("--unreachable-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
