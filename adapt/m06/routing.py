#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prove G2/G3-aware selection after eviction, using two dedicated KVBM workers."""

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import requests

from dynamo.llm import KvRouter, KvRouterConfig
from dynamo.runtime import DistributedRuntime


async def http(url, payload=None):
    def request():
        response = (
            requests.get(url, timeout=10)
            if payload is None
            else requests.post(url, json=payload, timeout=120)
        )
        response.raise_for_status()
        return response.text if url.endswith("/metrics") else response.json()

    return await asyncio.to_thread(request)


async def wait_for(check, label):
    deadline = time.monotonic() + 45
    last = None
    while time.monotonic() < deadline:
        ok, last = await check()
        if ok:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"{label}: {last}")


async def run(args, report):
    base = f"http://127.0.0.1:{args.http_port}"
    workers = []
    for port in args.worker_ports.split(","):
        metrics = await http(f"http://127.0.0.1:{port}/metrics")
        match = re.search(
            r'dynamo_component_inflight_requests\{[^\n]*dynamo_endpoint="generate"'
            r'[^\n]*worker_id="([0-9a-f]+)"',
            metrics,
        )
        assert match, "Missing worker identity"
        workers.append(int(match[1], 16))
    assert len(set(workers)) == 2
    report["workers"] = workers
    runtime = DistributedRuntime(
        asyncio.get_running_loop(), "file", "tcp", event_plane="zmq"
    )
    router = await asyncio.wait_for(
        asyncio.to_thread(
            KvRouter,
            runtime.endpoint(f"{args.namespace}.backend.generate"),
            args.block_size,
            KvRouterConfig(use_kv_events=True),
        ),
        timeout=30,
    )

    report["chat_smoke"] = await http(
        base + "/v1/chat/completions",
        {
            "model": "m05-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 8,
            "temperature": 0,
        },
    )

    async def generate(tokens, target=None):
        nvext = {"extra_fields": ["worker_id"]}
        if target is not None:
            nvext["decode_worker_id"] = target
        return await http(
            base + "/v1/completions",
            {
                "model": "m05-model",
                "prompt": tokens,
                "max_tokens": 8,
                "temperature": 0,
                "seed": 42,
                "nvext": nvext,
            },
        )

    # Distinct integer prefixes make the expected per-worker overlap explicit.
    prefix = [1000] * 640
    prompt = prefix + [100]
    await generate([101], workers[1])
    cold = await generate(prompt, workers[0])
    warm = await generate(prompt, workers[0])
    report.update(cold=cold, resident=warm)

    async def scores(g1):
        data = await router.get_overlap_scores(prompt, include_shared=False)
        rows = {row["worker_id"]: row for row in data["workers"]}
        owner = rows.get(workers[0], {})
        other = rows.get(workers[1], {})
        expected = 640 // args.block_size
        return (
            owner.get("device_blocks", 0) == (expected if g1 else 0)
            and (
                g1
                or (
                    owner.get("host_pinned_extension_blocks", 0) >= expected
                    if args.tier == "host"
                    else owner.get("host_pinned_blocks", 0) == 0
                    and owner.get("disk_extension_blocks", 0) >= expected
                )
            )
            and other.get("device_blocks", 0) == 0
            and other.get("host_pinned_blocks", 0) == 0,
            data,
        )

    report["device_scores"] = await wait_for(lambda: scores(True), "G1 overlap")
    # Fill more than the entire device pool while retaining the first G2 prefix.
    for token in (2000, 3000, 4000, 5000):
        await generate([token] * 640 + [100], workers[0])
        await asyncio.sleep(2)
    report["offloaded_scores"] = await wait_for(
        lambda: scores(False), f"{args.tier}-only overlap"
    )
    reload = await generate(prompt)
    assert reload["nvext"]["worker_id"]["decode_worker_id"] == workers[0], reload
    assert reload["choices"] == warm["choices"], "Reload differs from resident output"
    assert reload["usage"]["prompt_tokens_details"]["cached_tokens"] >= 640, reload
    report["reload"] = reload
    report["reloaded_scores"] = await wait_for(
        lambda: scores(True), f"G1 restored after {args.tier} reload"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--http-port", required=True, type=int)
    parser.add_argument("--worker-ports", required=True)
    parser.add_argument("--block-size", required=True, type=int)
    parser.add_argument("--tier", choices=("host", "disk"), default="host")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = {"status": "failed", "tier": args.tier}
    try:
        asyncio.run(run(args, report))
        report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
