#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise every discovered producer/consumer pair with a fresh KV handoff."""

import argparse
import asyncio
import json
from pathlib import Path

from dynamo.runtime import DistributedRuntime


async def run(args):
    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "etcd", "tcp", event_plane="zmq"
    )
    result = {"status": "failed", "pairs": []}
    try:
        producer = await runtime.endpoint(args.namespace + ".prefill.generate").client()
        consumer = await runtime.endpoint(args.namespace + ".backend.generate").client()
        async with asyncio.timeout(900):
            while True:
                pids, dids = (
                    sorted(producer.instance_ids()),
                    sorted(consumer.instance_ids()),
                )
                if (
                    len(pids) == args.prefill_workers
                    and len(dids) == args.decode_workers
                ):
                    break
                await asyncio.sleep(0.5)
        result.update(producers=pids, consumers=dids)
        reference = None
        for pid in pids:
            for did in dids:
                request = {
                    "model": args.model,
                    "token_ids": [1000, 1001] * 280,
                    "stop_conditions": {"max_tokens": 32},
                    "sampling_options": {"temperature": 0},
                }
                async with asyncio.timeout(60):
                    stream = await producer.direct(request, pid)
                    prefill = [item.data() async for item in stream]
                    if len(prefill) != 1 or not prefill[0].get("disaggregated_params"):
                        raise RuntimeError(f"Missing handoff from {pid}: {prefill}")
                    request["prefill_result"] = prefill[0]
                    stream = await consumer.direct(request, did)
                    decoded = [item.data() async for item in stream]
                tokens = [
                    token for item in decoded for token in item.get("token_ids", [])
                ]
                if not tokens or any(
                    item.get("finish_reason") == "error" for item in decoded
                ):
                    raise RuntimeError(f"Pair {pid}->{did} failed: {decoded}")
                if reference is None:
                    reference = tokens
                if tokens != reference:
                    raise RuntimeError(f"Pair {pid}->{did} token mismatch")
                result["pairs"].append(
                    {
                        "prefill": pid,
                        "decode": did,
                        "token_ids": tokens,
                        "finish_reason": decoded[-1].get("finish_reason"),
                    }
                )
        result["status"] = "passed"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(
            "REGRESSION_RESULT="
            + json.dumps({**result, "passed": result["status"] == "passed"}),
            flush=True,
        )
        runtime.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="regression-model")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--prefill-workers", type=int, required=True)
    parser.add_argument("--decode-workers", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
