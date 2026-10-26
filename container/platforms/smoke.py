#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test installed wheels in a disposable container without a source checkout."""

import argparse
import errno
import json
import os
import random
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def free_port(used):
    # DYN_SYSTEM_PORT is an i16; use the common valid range for all services.
    for _ in range(100):
        candidate = random.randint(20000, 30000)
        if candidate in used:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
                continue
        used.add(candidate)
        return candidate
    raise RuntimeError("Could not reserve a service port")


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", choices=["cuda", "metax", "ascend"], required=True
    )
    parser.add_argument("--devices", default="0,1")
    parser.add_argument("--model", default="/models/Qwen_Series/Qwen3-0.6B")
    parser.add_argument("--output", type=Path, default=Path("/tmp/m02-smoke"))
    args = parser.parse_args()
    devices = args.devices.split(",")
    if len(devices) != 2:
        parser.error("Use exactly two idle devices")
    args.output.mkdir(parents=True, exist_ok=False)
    from dynamo.common.platform.versions import validate_vllm_versions

    validate_vllm_versions(args.platform)
    used_ports = set()
    http = free_port(used_ports)
    env = {
        **os.environ,
        "DYN_NAMESPACE": "m02-smoke",
        "DYN_DISCOVERY_BACKEND": "file",
        "DYN_REQUEST_PLANE": "tcp",
        "DYN_EVENT_PLANE": "zmq",
        "DYN_FILE_KV": str(args.output / "discovery"),
        "DYN_HTTP_PORT": str(http),
        "DYN_ROUTER_USE_KV_EVENTS": "true",
        "HF_HUB_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONHASHSEED": "0",
    }
    processes = []
    handles = []
    report = {"passed": False, "platform": args.platform, "model": args.model}

    def start(name, command, overrides):
        handle = (args.output / f"{name}.log").open("w")
        handles.append(handle)
        proc = subprocess.Popen(
            command,
            env={**env, **overrides},
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        processes.append(proc)

    try:
        start(
            "frontend",
            [sys.executable, "-m", "dynamo.frontend", "--router-mode", "kv"],
            {"DYN_PLATFORM": "cpu", "DYN_SYSTEM_PORT": str(free_port(used_ports))},
        )
        block_size = 128 if args.platform == "ascend" else 64
        for index, device in enumerate(devices):
            visible = (
                "ASCEND_RT_VISIBLE_DEVICES"
                if args.platform == "ascend"
                else "CUDA_VISIBLE_DEVICES"
            )
            overrides = {
                "DYN_PLATFORM": args.platform,
                visible: device,
                "DYN_SYSTEM_PORT": str(free_port(used_ports)),
                "DYN_FORWARDPASS_METRIC_PORT": str(free_port(used_ports)),
            }
            if args.platform == "metax":
                overrides["MACA_VISIBLE_DEVICES"] = device
            events = {
                "publisher": "zmq",
                "topic": "kv-events",
                "endpoint": f"tcp://*:{free_port(used_ports)}",
                "enable_kv_cache_events": True,
            }
            command = [
                sys.executable,
                "-m",
                "dynamo.vllm",
                "--model",
                args.model,
                "--served-model-name",
                "m02-model",
                "--enforce-eager",
                "--enable-prefix-caching",
                "--block-size",
                str(block_size),
                "--max-model-len",
                "4096",
                "--max-num-seqs",
                "4",
                "--kv-cache-memory-bytes",
                "1073741824",
                "--kv-events-config",
                json.dumps(events),
            ]
            start(f"worker-{index}", command, overrides)
        url = f"http://127.0.0.1:{http}"
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("Service exited before readiness; inspect logs")
            try:
                ready = request(url + "/v1/models/m02-model/ready")
                role = (
                    ready.get("namespaces", {})
                    .get("m02-smoke", {})
                    .get("worker_types", {})
                    .get("aggregated", {})
                )
                if ready.get("ready") and role.get("workers") == 2:
                    report["ready"] = ready
                    break
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(2)
        else:
            raise TimeoutError("Two workers did not become ready within 900 seconds")
        base_payload = {
            "model": "m02-model",
            "max_tokens": 8,
            "temperature": 0,
            "nvext": {"extra_fields": ["worker_id", "timing"]},
        }
        basic = request(
            url + "/v1/completions",
            {**base_payload, "prompt": "The capital of France is"},
        )
        assert (
            basic["choices"][0]["text"] and basic["usage"]["completion_tokens"] > 0
        ), basic
        report["basic"] = basic
        # A cold unique prefix is cached on one worker. Subsequent requests must
        # choose that worker and report both engine cache reuse and router overlap.
        payload = {
            **base_payload,
            "prompt": list(range(1000, 1512)) + [100],
            "max_tokens": 1,
        }
        warm = request(url + "/v1/completions", payload)
        worker = warm["nvext"]["worker_id"]["decode_worker_id"]
        report["warm"] = warm
        time.sleep(3)  # Allow the asynchronous KV publication to reach the router.
        report["kv_repeats"] = []
        for _ in range(3):
            result = request(url + "/v1/completions", payload)
            assert result["nvext"]["worker_id"]["decode_worker_id"] == worker, result
            assert (
                result["usage"]["prompt_tokens_details"]["cached_tokens"] >= 512
            ), result
            assert result["nvext"]["timing"]["kv_hit_rate"] > 0, result
            report["kv_repeats"].append(result)
        report["passed"] = True
    finally:
        for proc in processes:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for proc in processes:
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        for handle in handles:
            handle.close()
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
