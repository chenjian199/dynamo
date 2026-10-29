#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""In-cluster API checks: readiness, repeat parity, SSE completion and cancellation."""

import argparse
import json
import time
import urllib.error
import urllib.request


def request(url, payload=None):
    req = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=120)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://regression-frontend:8000")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--namespace", default="regression")
    parser.add_argument("--prefill-workers", type=int, default=0)
    parser.add_argument("--model", default="regression-model")
    parser.add_argument(
        "--skip-router-kv-check",
        action="store_true",
        help="For explicitly uncached/round-robin configurations",
    )
    args = parser.parse_args()
    global REPORT
    report = REPORT = {"passed": False, "url": args.url, "requests": []}
    deadline = time.monotonic() + 900
    while True:
        try:
            with request(args.url + f"/v1/models/{args.model}/ready") as response:
                ready = json.load(response)
            roles = (
                ready.get("namespaces", {})
                .get(args.namespace, {})
                .get("worker_types", {})
            )
            count = roles.get(
                "decode" if args.prefill_workers else "aggregated", {}
            ).get("workers", 0)
            prefill = roles.get("prefill", {}).get("workers", 0)
            if (
                ready.get("ready")
                and count >= args.workers
                and prefill >= args.prefill_workers
            ):
                report["ready"] = ready
                break
        except (urllib.error.URLError, TimeoutError):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("workers readiness deadline")
        time.sleep(5)
    payload = {
        "model": args.model,
        "prompt": "The capital of France is",
        "temperature": 0,
        "seed": 42,
        "max_tokens": 16,
        "nvext": {"extra_fields": ["worker_id", "timing"]},
    }
    texts = []
    for _ in range(6):
        start = time.monotonic()
        with request(args.url + "/v1/completions", payload) as response:
            result = json.load(response)
        assert result["choices"][0]["text"]
        assert result["usage"]["completion_tokens"] > 0
        texts.append(result["choices"][0]["text"])
        report["requests"].append(
            {"seconds": time.monotonic() - start, "response": result}
        )
    assert len(set(texts)) == 1, "repeat greedy output mismatch"
    long_payload = {
        **payload,
        "prompt": "This is a stable prefix for cache routing. " * 128
        + "The capital of France is",
    }
    cached = []
    for _ in range(4):
        with request(args.url + "/v1/completions", long_payload) as response:
            cached.append(json.load(response))
        time.sleep(1)
    assert len({r["choices"][0]["text"] for r in cached}) == 1
    report["long_prefix"] = cached
    if not args.prefill_workers and not args.skip_router_kv_check:
        assert any(
            r.get("nvext", {}).get("timing", {}).get("kv_hit_rate", 0) > 0
            for r in cached[1:]
        ), "no router KV hit on repeated long prefix"
    with request(args.url + "/v1/completions", {**payload, "stream": True}) as response:
        pieces, done = [], False
        for line in response:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                done = True
                break
            event = json.loads(data)
            if event.get("choices"):
                pieces.append(event["choices"][0].get("text", ""))
        report["sse"] = {"done": done, "text": "".join(pieces), "reference": texts[0]}
        assert done and "".join(pieces) == texts[0], "SSE output mismatch"
    for _ in range(4):
        with request(
            args.url + "/v1/completions",
            {**payload, "max_tokens": 1024, "stream": True},
        ) as response:
            for line in response:
                if line.startswith(b"data:") and b"[DONE]" not in line:
                    event = json.loads(line[5:])
                    if event.get("choices") and event["choices"][0].get("text"):
                        break
            else:
                raise AssertionError("no stream content before cancellation")
        time.sleep(1)
    with request(args.url + "/v1/completions", payload) as response:
        recovery = json.load(response)
    assert recovery["choices"][0]["text"] == texts[0]
    report.update(
        passed=True, sse_parity=True, cancellations=4, post_cancel_recovery=True
    )
    print("REGRESSION_RESULT=" + json.dumps(report), flush=True)


if __name__ == "__main__":
    REPORT = {"passed": False}
    try:
        main()
    except Exception as error:
        REPORT["error"] = repr(error)
        print("REGRESSION_RESULT=" + json.dumps(REPORT), flush=True)
        raise
