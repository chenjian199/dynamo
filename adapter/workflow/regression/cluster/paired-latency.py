#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Three paired streaming measurements on the same allocated TP2 devices, sequential engines."""
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8010"
REPORT = {
    "passed": False,
    "phases": [],
    "qualification": "Three short functional latency runs, no throughput SLO or production capacity claim; same Pod/devices/weights and sequential native/Dynamo engines",
}


def query(path, payload=None):
    return urllib.request.urlopen(
        urllib.request.Request(
            BASE + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        ),
        timeout=120,
    )


def complete(_):
    payload = {
        "model": "perf-model",
        "prompt": [1000, 1001] * 64,
        "max_tokens": 64,
        "temperature": 0,
        "seed": 42,
        "ignore_eos": True,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    start = time.monotonic()
    first = None
    last = None
    usage = None
    chunks = []
    done = False
    with query("/v1/completions", payload) as response:
        for line in response:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                done = True
                break
            event = json.loads(data)
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices", []):
                text = choice.get("text", "")
                if text:
                    now = time.monotonic()
                    first = now if first is None else first
                    last = now
                    chunks.append(text)
    end = time.monotonic()
    assert done and first and usage and usage["completion_tokens"] == 64, (done, usage)
    return {
        "e2e_seconds": end - start,
        "ttft_seconds": first - start,
        "tpot_seconds": (last - first) / 63,
        "usage": usage,
        "text": "".join(chunks),
    }


def quantiles(values):
    values = sorted(values)
    return {
        f"p{p}": values[min(len(values) - 1, int(len(values) * p / 100))]
        for p in (50, 95, 99)
    }


def stop(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def main():
    platform = os.environ["DYN_PLATFORM"]
    reference = None
    common = [
        "--model",
        "/models/Qwen3-32B",
        "--served-model-name",
        "perf-model",
        "--tensor-parallel-size",
        "2",
        "--enforce-eager",
        "--enable-prefix-caching",
        "--block-size",
        "64" if platform == "cuda" else "128",
        "--max-model-len",
        "4096",
        "--max-num-seqs",
        "4",
        "--kv-cache-memory-bytes",
        "1073741824",
    ]
    for phase in ("native", "dynamo"):
        handles = []
        processes = []
        row = {"phase": phase, "runs": []}
        REPORT["phases"].append(row)
        env = {
            **os.environ,
            "DYN_DISCOVERY_BACKEND": "file",
            "DYN_FILE_KV": "/tmp/perf-discovery",
            "DYN_REQUEST_PLANE": "tcp",
            "DYN_EVENT_PLANE": "zmq",
            "DYN_NAMESPACE": "latency-test",
            "DYN_HTTP_PORT": "8010",
            "DYN_SYSTEM_PORT": "8081",
            "OMP_NUM_THREADS": "4",
        }
        commands = (
            [
                (
                    [
                        sys.executable,
                        "-m",
                        "vllm.entrypoints.openai.api_server",
                        *common,
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8010",
                    ],
                    env,
                )
            ]
            if phase == "native"
            else [
                (
                    [
                        sys.executable,
                        "-m",
                        "dynamo.frontend",
                        "--router-mode",
                        "round-robin",
                    ],
                    {**env, "DYN_PLATFORM": "cpu"},
                ),
                ([sys.executable, "-m", "dynamo.vllm", *common], env),
            ]
        )
        try:
            for index, (command, childenv) in enumerate(commands):
                log = Path(f"/tmp/{phase}-{index}.log")
                handle = log.open("w")
                handles.append((log, handle))
                processes.append(
                    subprocess.Popen(
                        command,
                        env=childenv,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
            deadline = time.monotonic() + 900
            while True:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("server exited")
                try:
                    with query("/v1/models") as response:
                        models = json.load(response)
                    if any(m["id"] == "perf-model" for m in models.get("data", [])):
                        break
                except (urllib.error.URLError, TimeoutError):
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("model readiness")
                time.sleep(3)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                warm = list(pool.map(complete, range(8)))
                if reference is None:
                    reference = warm[0]["text"]
                assert all(r["text"] == reference for r in warm), "warmup parity"
                for repetition in range(3):
                    start = time.monotonic()
                    rows = list(pool.map(complete, range(32)))
                    elapsed = time.monotonic() - start
                    assert all(
                        r["text"] == reference for r in rows
                    ), "native/Dynamo output parity"
                    result = {
                        "repetition": repetition,
                        "concurrency": 4,
                        "requests": 32,
                        "elapsed_seconds": elapsed,
                        "requests_per_second": 32 / elapsed,
                        "output_tokens_per_second": 2048 / elapsed,
                        "metrics": {
                            key: quantiles([r[key] for r in rows])
                            for key in ("e2e_seconds", "ttft_seconds", "tpot_seconds")
                        },
                        "samples": rows,
                    }
                    row["runs"].append(result)
                    print(
                        "LATENCY_RUN="
                        + json.dumps(
                            {k: v for k, v in result.items() if k != "samples"}
                        ),
                        flush=True,
                    )
            row["passed"] = True
        finally:
            for process in reversed(processes):
                stop(process)
            for log, handle in handles:
                handle.close()
                if not row.get("passed"):
                    print(
                        "SERVER_LOG="
                        + log.name
                        + "\n"
                        + log.read_text(errors="replace")[-18000:],
                        flush=True,
                    )
    REPORT["passed"] = True


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        REPORT["error"] = repr(error)
        raise
    finally:
        print("REGRESSION_RESULT=" + json.dumps(REPORT), flush=True)
