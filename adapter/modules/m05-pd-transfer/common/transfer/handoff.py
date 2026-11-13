#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Observe a PD handoff or inject a test-only fault without host network changes."""

import argparse
import asyncio
import copy
import json
import math
import time
import uuid
from pathlib import Path


async def run(args, runtime_factory=None):
    if runtime_factory is None:
        from dynamo.runtime import DistributedRuntime

        runtime_factory = DistributedRuntime
    runtime = runtime_factory(
        asyncio.get_running_loop(), args.discovery_backend, "tcp", event_plane="zmq"
    )
    result = {"case": args.case, "status": "failed"}
    try:
        producer = await runtime.endpoint(args.namespace + ".prefill.generate").client()
        consumer = await runtime.endpoint(args.namespace + ".backend.generate").client()
        await asyncio.wait_for(producer.wait_for_instances(), 10)
        await asyncio.wait_for(consumer.wait_for_instances(), 10)
        request = {
            "model": args.model,
            "token_ids": (
                json.loads(args.token_ids.read_text())
                if args.token_ids
                else [1000, 1001] * 280
            ),
            "stop_conditions": {"max_tokens": args.max_tokens},
            "sampling_options": {"temperature": 0},
        }
        if (
            not isinstance(request["token_ids"], list)
            or not request["token_ids"]
            or any(
                type(token) is not int or token < 0 for token in request["token_ids"]
            )
        ):
            raise ValueError(
                "token_ids must be a nonempty list of nonnegative integers"
            )
        result["request"] = copy.deepcopy(request)
        async with asyncio.timeout(args.timeout):
            stream = await producer.generate(request)
            outputs = [item.data() async for item in stream]
        if len(outputs) != 1 or not outputs[0].get("disaggregated_params"):
            raise RuntimeError(f"Unexpected prefill response: {outputs}")
        result["prefill_handoff"] = copy.deepcopy(outputs[0])
        handoff = copy.deepcopy(outputs[0])
        params = handoff["disaggregated_params"]["kv_transfer_params"]
        if args.case == "mismatch":
            params["_dynamo_pd_contract"]["block_size"] += 1
        elif args.case == "unreachable":
            if "remote_bootstrap_addr" in params:
                params[
                    "remote_bootstrap_addr"
                ] = f"http://127.0.0.1:{args.unreachable_port}"
            else:
                params["remote_port"] = args.unreachable_port
            # A new ID prevents reusing a cached successful handshake.
            params["remote_engine_id"] = str(uuid.uuid4())
        result["decode_handoff"] = copy.deepcopy(handoff)
        request["prefill_result"] = handoff
        start = time.monotonic()
        responses = []
        try:
            async with asyncio.timeout(args.timeout):
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
        if args.case == "valid":
            tokens = [
                token for item in responses for token in item.get("token_ids", [])
            ]
            if (
                not tokens
                or any(type(token) is not int or token < 0 for token in tokens)
                or len(tokens) > args.max_tokens
                or any(item.get("finish_reason") == "error" for item in responses)
                or responses[-1].get("finish_reason") not in ("stop", "length")
                or result["outcome"] != "backend_response"
            ):
                raise RuntimeError(f"Handoff did not complete successfully: {encoded}")
            if result["prefill_handoff"] != result["decode_handoff"]:
                raise RuntimeError(
                    "Successful handoff metadata was changed by the probe"
                )
            result["output_token_ids"] = tokens
            result["status"] = "passed"
        elif args.case == "mismatch":
            rejected = (
                result["outcome"] == "backend_error"
                and "Incompatible PD peer" in result.get("error", "")
            ) or any(
                item.get("finish_reason") == "error"
                and "Incompatible PD peer" in json.dumps(item)
                for item in responses
            )
            if not rejected:
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
    except Exception as exc:
        result["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        finally:
            runtime.shutdown()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument(
        "--case", choices=["valid", "mismatch", "unreachable"], required=True
    )
    parser.add_argument("--unreachable-port", type=int)
    parser.add_argument(
        "--discovery-backend", choices=["file", "etcd", "kubernetes"], default="file"
    )
    parser.add_argument("--model", default="m05-model")
    parser.add_argument("--token-ids", type=Path)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.case == "unreachable" and (
        args.unreachable_port is None or not 1 <= args.unreachable_port <= 65535
    ):
        parser.error("unreachable case requires --unreachable-port in 1..65535")
    if args.max_tokens <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("max-tokens and timeout must be positive")
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
