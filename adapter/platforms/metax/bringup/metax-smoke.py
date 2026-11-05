# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise an already running text server; no model loading or server management."""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from prometheus_client.parser import text_string_to_metric_families


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    return urlopen(
        Request(url, data=data, headers={"Content-Type": "application/json"}),
        timeout=60,
    )


def read_json(url, payload=None):
    with request(url, payload) as response:
        return json.load(response)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def counter(url, name, labels):
    with request(url) as response:
        text = response.read().decode()
    return sum(
        sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == name
        if all(sample.labels.get(key) == value for key, value in labels.items())
    )


def smoke(args):
    base = args.url.rstrip("/")
    with request(base + "/health") as response:
        require(response.status == 200, "Server is unhealthy")
    models = read_json(base + "/v1/models")
    require(args.model in [row["id"] for row in models["data"]], "Model not registered")

    payload = {
        "model": args.model,
        "prompt": "The capital of France is",
        "max_tokens": 16,
        "temperature": 0,
    }

    def complete(_):
        result = read_json(base + "/v1/completions", payload)
        choice = result["choices"][0]
        require(bool(choice["text"].strip()), "Empty completion")
        require(choice["finish_reason"] in ("stop", "length"), "Missing finish reason")
        require(result["usage"]["completion_tokens"] > 0, "Missing token usage")
        return result

    unary = complete(0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        concurrent = list(pool.map(complete, range(4)))

    chat = {
        "model": args.model,
        "messages": [{"role": "user", "content": "Say hello briefly."}],
        "max_tokens": 64,
        "temperature": 0,
        "stream": True,
    }
    chunks = []
    done = False
    with request(base + "/v1/chat/completions", chat) as response:
        for line in response:
            if not line.startswith(b"data: "):
                continue
            data = line[6:].strip()
            if data == b"[DONE]":
                done = True
                break
            chunk = json.loads(data)
            require("error" not in chunk, f"Stream failed: {chunk}")
            chunks.append(chunk)
    require(done and chunks, "Stream did not finish with [DONE]")
    require(
        any(
            choice.get("delta", {}).get(field)
            for chunk in chunks
            for choice in chunk.get("choices", [])
            for field in ("content", "reasoning", "reasoning_content")
        ),
        "Stream contains no generated text",
    )
    require(
        any(
            choice.get("finish_reason") in ("stop", "length")
            for chunk in chunks
            for choice in chunk.get("choices", [])
        ),
        "Stream lacks a finish reason",
    )

    cancellation = "not tested (native reference)"
    if args.worker_url:
        with request(args.worker_url.rstrip("/") + "/health") as response:
            require(response.status == 200, "Worker is unhealthy")
        metric_url = args.worker_url.rstrip("/") + "/metrics"
        metric_name = "dynamo_component_cancellation_total"
        labels = {"dynamo_namespace": args.namespace, "dynamo_endpoint": "generate"}
        before = counter(metric_url, metric_name, labels)
        long_request = {
            **payload,
            "stream": True,
            "max_tokens": 2048,
            "ignore_eos": True,
        }
        with request(base + "/v1/completions", long_request) as response:
            for line in response:
                if line.startswith(b"data: "):
                    data = line[6:].strip()
                    require(data != b"[DONE]", "Request ended before cancellation")
                    chunk = json.loads(data)
                    require(
                        "error" not in chunk, f"Cancellation stream failed: {chunk}"
                    )
                    if any(choice.get("text") for choice in chunk.get("choices", [])):
                        break
            else:
                raise RuntimeError("Cancellation request produced no token")
        deadline = time.monotonic() + 20
        while counter(metric_url, metric_name, labels) <= before:
            require(time.monotonic() < deadline, "Worker did not observe cancellation")
            time.sleep(0.2)
        complete(0)
        cancellation = {
            "worker_counter_before": before,
            "worker_counter_after": counter(metric_url, metric_name, labels),
            "follow_up_request": "passed",
        }

    return {
        "url": base,
        "model": args.model,
        "registered": True,
        "completion": unary,
        "concurrent_requests": len(concurrent),
        "stream_chunks": len(chunks),
        "stream_done": done,
        "cancellation": cancellation,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", default="Qwen3-0.6B")
    parser.add_argument(
        "--worker-url", help="Dynamo worker system endpoint; enables cancellation check"
    )
    parser.add_argument(
        "--namespace",
        default="metax_basic",
        help="Worker metric namespace label (Rust normalizes '-' to '_')",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = smoke(args)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
