#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded streaming load with measured TTFT/TPOT and a frozen baseline target.

Synthetic token IDs exercise scheduling, not semantic model quality. TPOT uses
API completion token counts, never SSE chunk counts. Transport/token failures
are recorded and cause nonzero exit; output determinism is not assumed.
"""
import argparse
import asyncio
import json
import math
import time

import aiohttp


def percentile(values, q=0.95):
    return sorted(values)[max(0, math.ceil(len(values) * q) - 1)] if values else None


async def request(session, args, sequence):
    start = time.perf_counter()
    first = last = None
    usage = None
    finished = False
    done = False
    token_ids = []
    visible_characters = 0
    payload = {
        "model": args.model,
        "prompt": [1000 + sequence % 100] * args.isl,
        "max_tokens": args.osl,
        "temperature": 0,
        "ignore_eos": True,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if args.token_id_metrics:
        payload["nvext"] = {"extra_fields": ["completion_token_ids"]}
    async with session.post(
        args.url.rstrip("/") + "/v1/completions", json=payload
    ) as response:
        if response.status != 200:
            raise RuntimeError(
                f"HTTP {response.status}: {(await response.text())[:500]}"
            )
        async for raw in response.content:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                done = True
                break
            event = json.loads(body)
            if event.get("error"):
                raise RuntimeError(str(event["error"]))
            if event.get("usage"):
                usage = event["usage"]
            ids = (event.get("nvext") or {}).get("completion_token_ids", [])
            if args.token_id_metrics and ids:
                token_ids.extend(ids)
                last = time.perf_counter()
                first = first or last
            for choice in event.get("choices", []):
                if choice.get("text"):
                    visible_characters += len(choice["text"])
                    if not args.token_id_metrics:
                        last = time.perf_counter()
                        first = first or last
                if choice.get("finish_reason"):
                    finished = True
    if (
        first is None
        or not finished
        or not done
        or not usage
        or usage.get("completion_tokens", 0) < 2
    ):
        raise RuntimeError(
            f"Incomplete stream/token accounting: done={done},finished={finished},usage={usage}"
        )
    if args.token_id_metrics and len(token_ids) != usage["completion_tokens"]:
        raise RuntimeError("Streamed token IDs do not match completion usage")
    return {
        "sequence": sequence,
        "completed_at": time.time(),
        "ttft_ms": (first - start) * 1000,
        "tpot_ms": (last - first) * 1000 / (usage["completion_tokens"] - 1),
        "e2e_ms": (time.perf_counter() - start) * 1000,
        "completion_tokens": usage["completion_tokens"],
        "prompt_tokens": usage["prompt_tokens"],
        "visible_characters": visible_characters,
    }


async def main(args):
    report = {
        "schema_version": 1,
        "model": args.model,
        "isl": args.isl,
        "osl": args.osl,
        "started_at": time.time(),
        "latency_basis": (
            "streamed_token_ids" if args.token_id_metrics else "visible_text"
        ),
        "stages": [],
        "errors": [],
    }
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=args.timeout)
    ) as session:
        baseline = []
        # Separate warmup from the calibration samples.
        for i in range(2):
            await request(session, args, i)
        for i in range(16):
            measurement = await request(session, args, i + 2)
            baseline.append(measurement)
            print("CALIBRATION=" + json.dumps(measurement), flush=True)
        targets = {
            "ttft_p95_ms": max(
                1000, 1.5 * percentile([r["ttft_ms"] for r in baseline])
            ),
            "tpot_p95_ms": max(50, 1.5 * percentile([r["tpot_ms"] for r in baseline])),
        }
        report["targets"] = targets
        report["baseline"] = baseline
        print(
            "BASELINE=" + json.dumps({"targets": targets, "measurements": baseline}),
            flush=True,
        )
        sequence = 100
        for label, concurrency, duration in [
            ("load", args.concurrency, args.duration),
            ("recovery", 1, args.recovery),
        ]:
            deadline = time.monotonic() + duration
            records = []

            async def worker():
                nonlocal sequence
                while time.monotonic() < deadline:
                    sequence += 1
                    current = sequence
                    try:
                        result = await request(session, args, current)
                        records.append(result)
                        print(
                            "REQUEST=" + json.dumps({"stage": label, **result}),
                            flush=True,
                        )
                    except (
                        aiohttp.ClientError,
                        asyncio.TimeoutError,
                        RuntimeError,
                        json.JSONDecodeError,
                    ) as error:
                        report["errors"].append(
                            {"stage": label, "sequence": current, "error": str(error)}
                        )
                        print(
                            "REQUEST_ERROR=" + json.dumps(report["errors"][-1]),
                            flush=True,
                        )
                        await asyncio.sleep(1)

            await asyncio.gather(*(worker() for _ in range(concurrency)))
            metrics = {
                "stage": label,
                "concurrency": concurrency,
                "requests": len(records),
                "empty_visible_text_requests": sum(
                    r["visible_characters"] == 0 for r in records
                ),
                "ttft_p95_ms": percentile([r["ttft_ms"] for r in records]),
                "tpot_p95_ms": percentile([r["tpot_ms"] for r in records]),
            }
            metrics["within_target"] = bool(records) and all(
                metrics[k] <= targets[k] for k in targets
            )
            report["stages"].append(metrics)
            print("STAGE=" + json.dumps(metrics), flush=True)
        report["completed_at"] = time.time()
        report["api_passed"] = not report["errors"] and all(
            s["requests"] > 0 for s in report["stages"]
        )
        print("RESULT=" + json.dumps(report), flush=True)
        if not report["api_passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", default="m09-model")
    parser.add_argument(
        "--token-id-metrics",
        action="store_true",
        help="Measure token delivery through nvext and validate IDs against usage; visible text may be empty",
    )
    parser.add_argument("--isl", type=int, default=128)
    parser.add_argument("--osl", type=int, default=256)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--recovery", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=180)
    asyncio.run(main(parser.parse_args()))
