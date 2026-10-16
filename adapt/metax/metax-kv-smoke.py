#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate real KV events and routing on two dedicated TP=1 workers.

Run inside the development container. This clears BOTH workers' prefix caches
and fills one cache to test eviction. Do not run against a shared service.
"""

import argparse
import asyncio
import collections
import hashlib
import importlib.metadata
import json
import math
import os
import random
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import msgspec
import zmq
import zmq.asyncio

from dynamo.llm import KvRouter, KvRouterConfig
from dynamo.runtime import DistributedRuntime


async def http(url, payload=None, *, raw=False):
    def request():
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read().decode()
        return body if raw else json.loads(body)

    return await asyncio.to_thread(request)


async def wait_until(check, label, timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        ok, last = await check()
        if ok:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"{label}: timed out; last result: {last}")


async def run(args, report):
    os.environ["DYN_FILE_KV"] = args.discovery_dir
    os.environ["DYN_SYSTEM_PORT"] = "0"
    os.environ["DYN_ROUTER_MIN_INITIAL_WORKERS"] = "2"
    ready = await http(f"{args.url}/v1/models/{args.model}/ready")
    role = ready["namespaces"][args.namespace]["worker_types"]["aggregated"]
    assert ready["ready"] and role["workers"] == 2, ready
    report["ready"] = ready
    workers = []
    for url in args.worker_urls:
        metrics = await http(f"{url}/metrics", raw=True)
        worker_id = re.search(
            r'dynamo_component_inflight_requests\{[^\n]*dynamo_endpoint="generate"'
            r'[^\n]*worker_id="([0-9a-f]+)"',
            metrics,
        )
        assert worker_id, "Missing worker identity metric"
        wid = int(worker_id[1], 16)
        # Capacity metrics are populated by the first forward-pass report.
        await http(
            f"{args.url}/v1/completions",
            {
                "model": args.model,
                "prompt": [100],
                "max_tokens": 1,
                "nvext": {"decode_worker_id": wid},
            },
        )

        async def capacity_ready():
            current = await http(f"{url}/metrics", raw=True)
            match = re.search(r"dynamo_component_total_blocks\{[^\n]*\} (\S+)", current)
            blocks = int(float(match[1])) if match else 0
            return blocks > 0, blocks

        capacity = await wait_until(capacity_ready, f"capacity of {wid}")
        workers.append({"id": wid, "url": url, "blocks": capacity})
    assert workers[0]["id"] != workers[1]["id"], workers
    assert all(0 < w["blocks"] <= 512 for w in workers), "Use the 1 GiB KV smoke cap"
    report["workers"] = workers

    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "file", "tcp", event_plane="zmq"
    )
    context = zmq.asyncio.Context()
    sockets, tasks = [], []
    event_stats = {}

    async def observe(socket, endpoint):
        stats = event_stats[endpoint]
        while True:
            frames = await socket.recv_multipart()
            batch = msgspec.msgpack.decode(frames[-1])
            rank = batch[2] if len(batch) > 2 else None
            stats["dp_ranks"].add(rank)
            for event in batch[1]:
                # vLLM 0.23 emits positional arrays; newer engines emit tagged
                # maps. Rust accepts both. Observe the original wire format.
                stats["wire_formats"].add("array" if isinstance(event, list) else "map")
                if isinstance(event, list):
                    kind = event[0]
                    fields = {
                        "BlockStored": (
                            "block_hashes",
                            "parent_block_hash",
                            "token_ids",
                            "block_size",
                        ),
                        "BlockRemoved": ("block_hashes",),
                        "AllBlocksCleared": (),
                    }[kind]
                    event = dict(type=kind, **dict(zip(fields, event[1:])))
                kind = event["type"]
                stats["counts"][kind] += 1
                if kind == "BlockStored":
                    stats["block_sizes"].add(event["block_size"])
                    stats["stored_blocks"] += len(event["block_hashes"])
                    if event["parent_block_hash"] is not None:
                        stats["stores_with_parent"] += 1
                elif kind == "BlockRemoved":
                    stats["removed_blocks"] += len(event["block_hashes"])

    try:
        endpoint = runtime.endpoint(f"{args.namespace}.backend.generate")
        router = await asyncio.wait_for(
            asyncio.to_thread(
                KvRouter, endpoint, args.block_size, KvRouterConfig(use_kv_events=True)
            ),
            timeout=30,
        )
        for address in args.event_endpoints:
            socket = context.socket(zmq.SUB)
            socket.setsockopt(zmq.SUBSCRIBE, b"kv-events")
            socket.connect(address)
            sockets.append(socket)
            event_stats[address] = {
                "counts": collections.Counter(),
                "wire_formats": set(),
                "dp_ranks": set(),
                "block_sizes": set(),
                "stored_blocks": 0,
                "removed_blocks": 0,
                "stores_with_parent": 0,
            }
            tasks.append(asyncio.create_task(observe(socket, address)))
        await asyncio.sleep(
            1
        )  # Allow ZMQ subscription propagation before clearing/seeding.

        clear_client = await runtime.endpoint(
            f"{args.namespace}.backend.clear_kv_blocks"
        ).client()

        async def clear_endpoints_ready():
            discovered = set(clear_client.instance_ids())
            return {worker["id"] for worker in workers} <= discovered, sorted(
                discovered
            )

        await wait_until(clear_endpoints_ready, "both clear_kv_blocks endpoints")

        async def clear(worker):
            stream = await clear_client.direct({}, worker["id"], annotated=False)
            replies = [reply async for reply in stream]
            assert len(replies) == 1, replies
            result = replies[0]
            assert result["status"] == "success", result
            return result

        async def scores(tokens, expected):
            async def check():
                result = await router.get_overlap_scores(tokens, include_shared=False)
                rows = {
                    (r["worker_id"], r["dp_rank"]): r["device_blocks"]
                    for r in result["workers"]
                }
                assert result["block_size"] == args.block_size, result
                return (
                    all(
                        rows.get((wid, 0)) == blocks for wid, blocks in expected.items()
                    ),
                    result,
                )

            return await wait_until(check, f"overlap {expected}")

        async def generate(tokens, forced=None):
            nvext = {"extra_fields": ["worker_id", "timing"]}
            if forced is not None:
                nvext["decode_worker_id"] = forced
            result = await http(
                f"{args.url}/v1/completions",
                {
                    "model": args.model,
                    "prompt": tokens,
                    "max_tokens": 1,
                    "temperature": 0,
                    "nvext": nvext,
                },
            )
            assert (
                result["choices"] and result["usage"]["completion_tokens"] == 1
            ), result
            return {
                "worker": result["nvext"]["worker_id"],
                "timing": result["nvext"]["timing"],
                "usage": result["usage"],
            }

        def routed(result, worker, cached):
            assert result["worker"]["decode_worker_id"] == worker, result
            assert result["worker"]["decode_dp_rank"] == 0, result
            assert (
                result["usage"]["prompt_tokens_details"]["cached_tokens"] == cached
            ), result

        await asyncio.gather(*(clear(w) for w in workers))
        rng = random.Random(args.seed)
        a, b, c, d, e, f, g = [
            [rng.randint(1000, 20000) for _ in range(args.block_size)] for _ in range(7)
        ]
        wa, wb = (w["id"] for w in workers)
        tail = [100]
        # Two competing branches share A+C; match depth must select the right leaf.
        seeds = []
        for tokens, target in (
            (a + b + tail, wa),
            (a + c + d + tail, wa),
            (a + c + e + tail, wb),
        ):
            result = await generate(tokens, target)
            assert result["worker"]["decode_worker_id"] == target, result
            seeds.append(result)
        report["seeds"] = seeds
        report["branches"] = []
        for tokens, target, expected in (
            (a + c + d + f + tail, wa, {wa: 3, wb: 2}),
            (a + c + e + g + tail, wb, {wa: 2, wb: 3}),
        ):
            overlap = await scores(tokens, expected)
            result = await generate(tokens)
            routed(result, target, 3 * args.block_size)
            assert result["timing"]["kv_hit_rate"] > 0, result
            report["branches"].append({"overlap": overlap, "response": result})
        # Repeats use no worker override and must continue to choose the cached branch.
        report["repeats"] = []
        for _ in range(3):
            for tokens, target in (
                (a + c + d + f + tail, wa),
                (a + c + e + g + tail, wb),
            ):
                await scores(
                    tokens, {wa: 4 if target == wa else 2, wb: 4 if target == wb else 2}
                )
                result = await generate(tokens)
                routed(result, target, 4 * args.block_size)
                report["repeats"].append(result)

        cold = [rng.randint(1000, 20000) for _ in range(4 * args.block_size)] + tail
        report["cold_overlap"] = await scores(cold, {wa: 0, wb: 0})
        result = await generate(cold)
        assert result["usage"]["prompt_tokens_details"]["cached_tokens"] == 0, result
        assert result["timing"]["kv_hit_rate"] == 0, result
        report["cold_response"] = result

        pressure_count = math.ceil(workers[0]["blocks"] / 16) + 1
        for _ in range(pressure_count):
            await generate(
                [rng.randint(1000, 20000) for _ in range(16 * args.block_size)] + tail,
                wa,
            )
        report["eviction"] = {
            "pressure_requests": pressure_count,
            "overlap_after": await scores(a + c + d + f + tail, {wa: 0, wb: 2}),
        }
        report["clear"] = await asyncio.gather(*(clear(w) for w in workers))
        report["after_clear_overlap"] = await scores(
            a + c + e + g + tail, {wa: 0, wb: 0}
        )
        result = await generate(a + c + e + g + tail)
        assert result["usage"]["prompt_tokens_details"]["cached_tokens"] == 0, result
        assert result["timing"]["kv_hit_rate"] == 0, result
        report["after_clear_response"] = result
        target = result["worker"]["decode_worker_id"]
        await scores(
            a + c + e + g + tail,
            {wa: 4 if target == wa else 0, wb: 4 if target == wb else 0},
        )
        result = await generate(a + c + e + g + tail)
        routed(result, target, 4 * args.block_size)
        report["after_clear_rewarm"] = result
        await asyncio.sleep(0.5)
        for task in tasks:
            if task.done():
                task.result()  # Surface decoding/transport errors instead of a false pass.
        for stats in event_stats.values():
            assert stats["block_sizes"] == {args.block_size}, stats
            assert stats["dp_ranks"] == {0}, stats
            assert stats["counts"]["BlockStored"] > 0, stats
            assert stats["stores_with_parent"] > 0, stats
            assert stats["counts"]["AllBlocksCleared"] >= 2, stats
        assert event_stats[args.event_endpoints[0]]["removed_blocks"] > 0, event_stats
        report["passed"] = True
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for socket in sockets:
            socket.close(linger=0)
        context.term()
        runtime.shutdown()
        report["events"] = {
            address: {
                key: sorted(value) if isinstance(value, set) else value
                for key, value in stats.items()
            }
            for address, stats in event_stats.items()
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument(
        "--worker-urls",
        nargs=2,
        default=["http://127.0.0.1:8082", "http://127.0.0.1:8083"],
    )
    parser.add_argument(
        "--event-endpoints",
        nargs=2,
        default=["tcp://127.0.0.1:20082", "tcp://127.0.0.1:20083"],
    )
    parser.add_argument("--platform", choices=("metax", "ascend"), default="metax")
    parser.add_argument("--namespace", default="metax-kv")
    parser.add_argument("--discovery-dir", default="/tmp/dynamo-metax-kv")
    parser.add_argument("--model", default="Qwen3-0.6B")
    parser.add_argument("--block-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    report = {
        "passed": False,
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=root
        ).strip(),
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "components/src/dynamo/vllm/instrumented_scheduler.py",
                f"adapt/{args.platform}/{args.platform}-kv-serve.sh",
                "adapt/metax/metax-kv-smoke.py",
            )
        },
        "config": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "ai-dynamo",
                "ai-dynamo-runtime",
                "vllm",
                f"vllm-{args.platform}",
                "torch",
            )
        },
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    try:
        asyncio.run(asyncio.wait_for(run(args, report), timeout=240))
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"passed={report['passed']}; evidence={args.output}")


if __name__ == "__main__":
    main()
