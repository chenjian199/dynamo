#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""DP rank-directed API checks plus bounded input/concurrency sweeps."""

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "http://dense-dp-frontend:8000"
BASE = {
    "model": "dense-dp-model",
    "temperature": 0,
    "seed": 42,
    "max_tokens": 16,
    "nvext": {"extra_fields": ["worker_id", "timing"]},
}


def complete(payload):
    started = time.monotonic()
    req = urllib.request.Request(
        URL + "/v1/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as response:
        result = json.load(response)
    assert result["choices"][0]["text"], result
    return {"seconds": time.monotonic() - started, "response": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact-long", action="store_true")
    args = parser.parse_args()
    deadline = time.monotonic() + 900
    while True:
        try:
            with urllib.request.urlopen(
                URL + "/v1/models/dense-dp-model/ready", timeout=10
            ) as response:
                if json.load(response).get("ready"):
                    break
        except (urllib.error.URLError, TimeoutError):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("DP worker readiness")
        time.sleep(5)
    report = {"passed": False, "ranks": [], "ladder": []}
    try:
        first = complete({**BASE, "prompt": "The capital of France is"})
        worker = first["response"]["nvext"]["worker_id"]["decode_worker_id"]
        for rank in (0, 1):
            prompt = ("This distinct prefix belongs to rank " + str(rank) + ". ") * 40
            payload = {
                **BASE,
                "prompt": prompt,
                "nvext": {**BASE["nvext"], "decode_worker_id": worker, "dp_rank": rank},
            }
            cold = complete(payload)
            warm = complete(payload)
            assert (
                cold["response"]["nvext"]["worker_id"]["decode_dp_rank"] == rank
            ), cold
            assert (
                warm["response"]["nvext"]["worker_id"]["decode_dp_rank"] == rank
            ), warm
            assert (
                warm["response"]["usage"]["prompt_tokens_details"]["cached_tokens"] > 0
            ), warm
            assert (
                cold["response"]["choices"][0]["text"]
                == warm["response"]["choices"][0]["text"]
            )
            report["ranks"].append({"rank": rank, "cold": cold, "warm": warm})
        for repeats, output in (
            [(4096, 128)] if args.exact_long else [(12, 64), (100, 128), (800, 128)]
        ):
            payload = {
                **BASE,
                "prompt": "This is a stable prefix for cache routing. " * repeats
                + "The capital of France is",
                "max_tokens": output,
            }
            if args.exact_long:
                payload["prompt"] = [1000, 1001] * repeats
            reference = complete(payload)["response"]["choices"][0]["text"]
            for concurrency in (1, 4, 16):
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    rows = list(
                        pool.map(
                            lambda _: complete(payload), range(max(3, concurrency))
                        )
                    )
                entry = {
                    "prefix_repeats": repeats,
                    "output_limit": output,
                    "concurrency": concurrency,
                    "responses": rows,
                }
                report["ladder"].append(entry)
                if args.exact_long:
                    assert all(
                        row["response"]["usage"]["prompt_tokens"] == 8192
                        for row in rows
                    )
                    entry["exact_prompt_tokens"] = 8192
                assert all(
                    r["response"]["choices"][0]["text"] == reference for r in rows
                ), "ladder greedy parity"
                print(
                    json.dumps(
                        {
                            "prefix_repeats": repeats,
                            "concurrency": concurrency,
                            "passed": True,
                        }
                    ),
                    flush=True,
                )
        report["passed"] = True
    finally:
        print("REGRESSION_RESULT=" + json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
