#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded API load; process/native leak qualification is a separate observation."""

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--seconds", type=int, default=1800)
    p.add_argument("--minimum-requests", type=int, default=1000)
    p.add_argument("--warmup-seconds", type=int, default=0)
    p.add_argument("--ready-workers", type=int, default=0)
    p.add_argument("--ready-prefill", type=int, default=0)
    p.add_argument(
        "--observe-output-parity",
        action="store_true",
        help="Record output variants separately from API stability; default is strict parity",
    )
    args = p.parse_args()
    if args.ready_workers:
        deadline = time.monotonic() + 900
        while True:
            try:
                with urllib.request.urlopen(
                    args.url + f"/v1/models/{args.model}/ready", timeout=10
                ) as response:
                    ready = json.load(response)
                groups = [
                    v.get("worker_types", {})
                    for v in ready.get("namespaces", {}).values()
                ]
                if ready.get("ready") and any(
                    g.get("decode", {}).get("workers", 0) >= args.ready_workers
                    and g.get("prefill", {}).get("workers", 0) >= args.ready_prefill
                    for g in groups
                ):
                    break
            except (urllib.error.URLError, TimeoutError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("PD readiness")
            time.sleep(5)
    payload = json.dumps(
        {
            "model": args.model,
            "prompt": "The capital of France is",
            "temperature": 0,
            "seed": 42,
            "max_tokens": 16,
            "nvext": {"extra_fields": ["worker_id", "timing"]},
        }
    ).encode()

    def complete(_):
        start = time.monotonic()
        req = urllib.request.Request(
            args.url + "/v1/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
        assert result["choices"][0]["text"]
        return time.monotonic() - start, result

    # Warm caches and both batch/concurrency paths before the measurement window.
    variants = Counter()

    def check_output(result):
        text = result["choices"][0]["text"]
        variants[text] += 1
        if not args.observe_output_parity:
            assert text == reference, "load output changed"

    with ThreadPoolExecutor(max_workers=4) as pool:
        warm = list(pool.map(complete, range(16)))
        reference = warm[0][1]["choices"][0]["text"]
        for _, result in warm:
            check_output(result)
        warm_deadline = time.monotonic() + args.warmup_seconds
        while time.monotonic() < warm_deadline:
            warm = list(pool.map(complete, range(16)))
            for _, result in warm:
                check_output(result)
        print("WARMUP_COMPLETE", flush=True)
        durations, workers, timings = [], set(), []
        start = time.monotonic()
        while (
            time.monotonic() - start < args.seconds
            or len(durations) < args.minimum_requests
        ):
            for elapsed, result in pool.map(complete, range(16)):
                check_output(result)
                durations.append(elapsed)
                ext = result.get("nvext", {})
                workers.add(str(ext.get("worker_id")))
                timings.append(ext.get("timing", {}))
            print(
                json.dumps(
                    {"completed": len(durations), "elapsed": time.monotonic() - start}
                ),
                flush=True,
            )
            time.sleep(1)
    ordered = sorted(durations)
    print(
        "REGRESSION_RESULT="
        + json.dumps(
            {
                "passed": True,
                "qualification": "API stability only; resource/native leak checks reported separately",
                "parity_mode": "observe" if args.observe_output_parity else "strict",
                "deterministic": len(variants) == 1,
                "output_variants_including_warmup": dict(variants),
                "model": args.model,
                "url": args.url,
                "completed": len(durations),
                "elapsed": time.monotonic() - start,
                "concurrency": 4,
                "latency_seconds": {
                    "mean": statistics.mean(durations),
                    **{
                        f"p{n}": ordered[
                            min(len(ordered) - 1, int(len(ordered) * n / 100))
                        ]
                        for n in [50, 95, 99]
                    },
                },
                "workers": sorted(workers),
                "first_timing": timings[0],
                "last_timing": timings[-1],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
