#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot worker process resources from inside its own Pod PID namespace."""

import json
import os
import time
import urllib.request
from pathlib import Path

import psutil

result = {"time": time.time(), "processes": {}}
for proc in psutil.process_iter(["pid", "name", "cmdline"]):
    if proc.pid == os.getpid():
        continue
    name = proc.info["name"]
    command = " ".join(proc.info["cmdline"] or [])
    if not any(
        s in command + name for s in ["dynamo.vllm", "vllm", "VLLM", "spawn_main"]
    ):
        continue
    with proc.oneshot():
        result["processes"][str(proc.pid)] = {
            "name": name,
            "created": proc.create_time(),
            "rss": proc.memory_info().rss,
            "fds": proc.num_fds(),
            "threads": proc.num_threads(),
        }
assert result["processes"], "no worker processes found"
observer = os.environ.get("M05_OBSERVER_DIR")
if observer:
    result["native"] = {
        p.name: json.loads(p.read_text()) for p in Path(observer).glob("native-*.json")
    }
try:
    port = os.environ.get("DYN_SYSTEM_PORT", "8081")
    with urllib.request.urlopen(
        "http://127.0.0.1:" + port + "/metrics", timeout=10
    ) as response:
        result["occupancy"] = [
            line
            for line in response.read().decode().splitlines()
            if not line.startswith("#")
            and any(
                line.startswith(key + "{")
                for key in (
                    "vllm:num_requests_running",
                    "vllm:num_requests_waiting",
                    "vllm:kv_cache_usage_perc",
                )
            )
        ]
except Exception as error:
    result["metrics_error"] = repr(error)
print(json.dumps(result))
