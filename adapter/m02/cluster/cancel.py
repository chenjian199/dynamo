#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Separate 32-stream cancellation/recovery phase after the steady load window."""

import json
import time
import urllib.request

url = "http://pd-frontend:8000/v1/completions"
payload = {
    "model": "regression-model",
    "prompt": "The capital of France is",
    "temperature": 0,
    "seed": 42,
    "max_tokens": 16,
}


def request(body):
    return urllib.request.urlopen(
        urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        ),
        timeout=120,
    )


with request(payload) as response:
    reference = json.load(response)["choices"][0]["text"]
started = time.monotonic()
for i in range(32):
    with request(
        {**payload, "max_tokens": 2048, "stream": True, "ignore_eos": True}
    ) as response:
        for line in response:
            if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                event = json.loads(line[5:])
                if event.get("choices") and event["choices"][0].get("text"):
                    break
        else:
            raise AssertionError("no content before stream cancellation")
    with request(payload) as response:
        assert json.load(response)["choices"][0]["text"] == reference
print(
    "REGRESSION_RESULT="
    + json.dumps(
        {
            "passed": True,
            "cancellations": 32,
            "recovery_requests": 32,
            "elapsed": time.monotonic() - started,
        }
    ),
    flush=True,
)
