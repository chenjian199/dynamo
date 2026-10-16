#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check per-rank cache routing and invalidation on a dedicated M03 service."""

import argparse
import asyncio
import importlib.util
import json
import os
import random
import re
from pathlib import Path

import msgspec
import zmq
import zmq.asyncio
from prometheus_client.parser import text_string_to_metric_families

from dynamo.llm import KvRouter, KvRouterConfig
from dynamo.runtime import DistributedRuntime

SPEC = importlib.util.spec_from_file_location(
    "kv_smoke", Path(__file__).parents[1] / "metax/metax-kv-smoke.py"
)
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)
http, wait_until = HELPERS.http, HELPERS.wait_until


async def run(args, report):
    os.environ.update(
        DYN_FILE_KV=f"/tmp/dynamo-m03-{args.platform}",
        DYN_SYSTEM_PORT="0",
        DYN_ROUTER_MIN_INITIAL_WORKERS="1",
    )
    namespace = f"m03-{args.platform}"
    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "file", "tcp", event_plane="zmq"
    )
    context = zmq.asyncio.Context()
    sockets, tasks = [], []
    events = {rank: [] for rank in range(args.dp)}
    report["events"] = events
    try:
        metrics = await http("http://127.0.0.1:8090/metrics", raw=True)
        match = re.search(
            r'dynamo_component_inflight_requests\{[^\n]*dynamo_endpoint="generate"[^\n]*worker_id="([0-9a-f]+)"',
            metrics,
        )
        assert match, "Missing worker identity"
        worker = int(match[1], 16)
        report["worker_id"] = worker
        client = await runtime.endpoint(f"{namespace}.backend.generate").client()
        await asyncio.wait_for(client.wait_for_instances(), timeout=30)
        instances = client.instances()
        assert len(instances) == 1, instances
        descriptor = instances[0].platform
        assert descriptor["platform_id"] == args.platform, descriptor
        assert (
            descriptor["capabilities"]["kv.events"]["state"] == "available"
        ), descriptor
        assert len({device["uuid"] for device in descriptor["devices"]}) == len(
            descriptor["devices"]
        ), descriptor
        report["platform"] = descriptor
        router = await asyncio.to_thread(
            KvRouter,
            runtime.endpoint(f"{namespace}.backend.generate"),
            args.block_size,
            KvRouterConfig(use_kv_events=True),
        )

        async def observe(socket, rank):
            while True:
                frames = await socket.recv_multipart()
                batch = msgspec.msgpack.decode(frames[-1])
                assert batch[2] == rank, batch
                for event in batch[1]:
                    kind = event[0] if isinstance(event, list) else event["type"]
                    events[rank].append(kind)

        for rank in range(args.dp):
            socket = context.socket(zmq.SUB)
            socket.setsockopt(zmq.SUBSCRIBE, b"kv-events")
            socket.connect(f"tcp://127.0.0.1:{20092 + rank}")
            sockets.append(socket)
            tasks.append(asyncio.create_task(observe(socket, rank)))
        clear = await runtime.endpoint(f"{namespace}.backend.clear_kv_blocks").client()

        async def discovered():
            ids = clear.instance_ids()
            return worker in ids, ids

        await wait_until(discovered, "clear endpoint")
        await asyncio.sleep(1)

        async def clear_cache():
            stream = await clear.direct({}, worker, annotated=False)
            replies = [reply async for reply in stream]
            assert len(replies) == 1 and replies[0]["status"] == "success", replies

        async def generate(tokens, rank=None):
            nvext = {"extra_fields": ["worker_id", "timing"]}
            if rank is not None:
                nvext.update(decode_worker_id=worker, dp_rank=rank)
            result = await http(
                "http://127.0.0.1:8010/v1/completions",
                {
                    "model": "m03-model",
                    "prompt": tokens,
                    "max_tokens": 1,
                    "temperature": 0,
                    "nvext": nvext,
                },
            )
            assert result["usage"]["completion_tokens"] == 1, result
            if rank is not None:
                assert result["nvext"]["worker_id"]["decode_dp_rank"] == rank, result
            return result

        async def scores(tokens, target):
            async def check():
                result = await router.get_overlap_scores(tokens, include_shared=False)
                rows = {
                    row["dp_rank"]: row["device_blocks"]
                    for row in result["workers"]
                    if row["worker_id"] == worker
                }
                return (
                    all(
                        rows.get(rank) == (2 if rank == target else 0)
                        for rank in range(args.dp)
                    ),
                    result,
                )

            return await wait_until(check, f"rank overlap {target}")

        await clear_cache()
        rng = random.Random(20261016)
        prompts = [
            [rng.randint(1000, 20000) for _ in range(2 * args.block_size)] + [100]
            for _ in range(args.dp)
        ]
        report["seeds"] = await asyncio.gather(
            *(generate(prompt, rank) for rank, prompt in enumerate(prompts))
        )
        report["warm"] = []
        for rank, prompt in enumerate(prompts):
            overlap = await scores(prompt, rank)
            result = await generate(prompt)
            assert result["nvext"]["worker_id"]["decode_dp_rank"] == rank, result
            assert (
                result["usage"]["prompt_tokens_details"]["cached_tokens"]
                == 2 * args.block_size
            ), result
            report["warm"].append(dict(overlap=overlap, response=result))

        async def capacity_ready():
            text = await http("http://127.0.0.1:8090/metrics", raw=True)
            rows = [
                dict(labels=sample.labels, value=sample.value)
                for family in text_string_to_metric_families(text)
                for sample in family.samples
                if sample.name == "dynamo_component_total_blocks"
            ]
            return (
                len(rows) == args.dp
                and {row["labels"].get("dp_rank") for row in rows}
                == {str(rank) for rank in range(args.dp)}
                and all(row["value"] == args.expected_blocks for row in rows)
            ), rows

        report["capacity"] = await wait_until(capacity_ready, "per-rank capacity")
        await clear_cache()
        for prompt in prompts:
            await scores(prompt, None)
        for rank, prompt in enumerate(prompts):
            result = await generate(prompt, rank)
            assert (
                result["usage"]["prompt_tokens_details"]["cached_tokens"] == 0
            ), result
        await asyncio.sleep(0.5)
        for task in tasks:
            if task.done():
                task.result()
        for rank, observed in events.items():
            assert (
                "BlockStored" in observed and observed.count("AllBlocksCleared") >= 2
            ), (rank, observed)
        report["passed"] = True
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for socket in sockets:
            socket.close(linger=0)
        context.term()
        runtime.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", choices=("metax", "ascend", "cuda"), required=True
    )
    parser.add_argument("--dp", type=int, required=True)
    parser.add_argument("--block-size", type=int, required=True)
    parser.add_argument(
        "--expected-blocks",
        type=int,
        required=True,
        help="Physical tokens per rank from engine log divided by block size",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = dict(
        passed=False, config={key: str(value) for key, value in vars(args).items()}
    )
    try:
        asyncio.run(asyncio.wait_for(run(args, report), 180))
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
