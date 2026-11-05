#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Corrupt only a test request's PD handoff, then require fresh-request recovery."""

import argparse
import asyncio
import copy
import json
import time
import uuid

from dynamo.runtime import DistributedRuntime


async def run(args, report):
    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "etcd", "tcp", event_plane="zmq"
    )
    try:
        producer = await runtime.endpoint(args.namespace + ".prefill.generate").client()
        consumer = await runtime.endpoint(args.namespace + ".backend.generate").client()
        async with asyncio.timeout(900):
            while not producer.instance_ids() or not consumer.instance_ids():
                await asyncio.sleep(1)
        pid, did = (
            sorted(producer.instance_ids())[0],
            sorted(consumer.instance_ids())[0],
        )
        report.update(producer=pid, consumer=did)
        request = {
            "model": args.model,
            "token_ids": [1000, 1001] * 280,
            "stop_conditions": {"max_tokens": 32},
            "sampling_options": {"temperature": 0},
        }

        async def prefill():
            async with asyncio.timeout(90):
                stream = await producer.direct(copy.deepcopy(request), pid)
                outputs = [item.data() async for item in stream]
            if len(outputs) != 1 or not outputs[0].get("disaggregated_params"):
                raise AssertionError(f"Missing handoff: {outputs}")
            return outputs[0]

        async def decode(handoff):
            data = copy.deepcopy(request)
            data["prefill_result"] = handoff
            stream = await consumer.direct(data, did)
            return [item.data() async for item in stream]

        def tokens(outputs):
            value = [t for row in outputs for t in row.get("token_ids", [])]
            assert value and not any(
                row.get("finish_reason") == "error" for row in outputs
            ), outputs
            return value

        async with asyncio.timeout(90):
            reference = tokens(await decode(await prefill()))
        report["reference_tokens"] = reference
        for case in ("mismatch", "unreachable"):
            row = {"case": case, "passed": False}
            report["cases"].append(row)
            handoff = await prefill()
            params = handoff["disaggregated_params"]["kv_transfer_params"]
            if case == "mismatch":
                params["_dynamo_pd_contract"]["block_size"] += 1
            else:
                # Loopback inside the chosen worker; no real remote service is affected.
                if "remote_bootstrap_addr" in params:
                    params["remote_bootstrap_addr"] = "http://127.0.0.1:9"
                else:
                    params["remote_port"] = 9
                params["remote_engine_id"] = str(uuid.uuid4())
            start = time.monotonic()
            try:
                async with asyncio.timeout(90):
                    row["responses"] = await decode(handoff)
                row["outcome"] = "backend_response"
            except TimeoutError:
                row["outcome"] = "client_timeout_without_backend_completion"
            except Exception as error:
                row.update(outcome="backend_error", error=str(error))
            row["elapsed_seconds"] = time.monotonic() - start
            if case == "mismatch":
                assert "Incompatible PD peer" in json.dumps(row), row
            else:
                assert any(
                    item.get("finish_reason") == "error"
                    for item in row.get("responses", [])
                ), row
            # The real producer did not receive a consumer acknowledgement for the bad handoff.
            await asyncio.sleep(32)
            async with asyncio.timeout(90):
                row["recovery_tokens"] = tokens(await decode(await prefill()))
            assert row["recovery_tokens"] == reference, row
            row["passed"] = True
            print("FAULT_CASE=" + json.dumps(row), flush=True)
        report["passed"] = True
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    report = {
        "passed": False,
        "cases": [],
        "qualification": "Request-local faults; explicit error and fresh request recovery, not in-flight migration",
    }
    try:
        asyncio.run(run(args, report))
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        print("REGRESSION_RESULT=" + json.dumps(report), flush=True)
