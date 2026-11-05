#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise an existing vision model with images/video, streaming and cancellation."""

import argparse
import base64
import io
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

URL = "http://vision-frontend:8000"


def request(path, payload=None):
    req = urllib.request.Request(
        URL + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=180)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model-label", default="Qwen2.5-VL-72B-Instruct")
    p.add_argument("--h264", action="store_true")
    p.add_argument("--disable-thinking", action="store_true")
    args = p.parse_args()
    deadline = time.monotonic() + 1500
    while True:
        try:
            with request("/v1/models/vision-model/ready") as response:
                if json.load(response).get("ready"):
                    break
        except (urllib.error.URLError, TimeoutError):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("vision worker readiness")
        time.sleep(5)
    report = {"passed": False, "model": args.model_label, "prefix_cache": False}
    payloads = []
    for color in ("red", "blue"):
        buffer = io.BytesIO()
        Image.new("RGB", (448, 448), color).save(buffer, format="PNG")
        uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        payloads.append(
            {
                "model": "vision-model",
                "temperature": 0,
                "max_tokens": 24,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "What color is the image? Answer with one color word.",
                            },
                            {"type": "image_url", "image_url": {"url": uri}},
                        ],
                    }
                ],
            }
        )
    if args.disable_thinking:
        for payload in payloads:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

    def complete(payload):
        with request("/v1/chat/completions", payload) as response:
            return json.load(response)

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(complete, payloads * 2))
        for color, result in zip(("red", "blue") * 2, responses):
            assert (
                color in (result["choices"][0]["message"]["content"] or "").lower()
            ), result
        report["images"] = responses
        with request(
            "/v1/chat/completions", {**payloads[0], "stream": True}
        ) as response:
            chunks = []
            done = False
            for line in response:
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    done = True
                    break
                event = json.loads(data)
                chunks.extend(
                    choice.get("delta", {}).get("content") or ""
                    for choice in event.get("choices", [])
                )
            assert done and "red" in "".join(chunks).lower(), chunks
            report["stream"] = {"done": done, "text": "".join(chunks)}
        for _ in range(4):
            with request(
                "/v1/chat/completions",
                {**payloads[0], "stream": True, "max_tokens": 1024, "ignore_eos": True},
            ) as response:
                for line in response:
                    if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                        event = json.loads(line[5:])
                        if any(
                            c.get("delta", {}).get("content")
                            for c in event.get("choices", [])
                        ):
                            break
                else:
                    raise AssertionError("no text before cancellation")
        report["after_cancel"] = complete(payloads[1])
        assert (
            "blue" in report["after_cancel"]["choices"][0]["message"]["content"].lower()
        )
        invalid = {
            **payloads[0],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,bm90LWFuLWltYWdl"
                            },
                        }
                    ],
                }
            ],
        }
        try:
            complete(invalid)
        except urllib.error.HTTPError as error:
            report["invalid_image"] = {
                "status": error.code,
                "body": error.read().decode(),
            }
        else:
            raise AssertionError("invalid image accepted")
        video = (
            "data:video/mp4;base64," if args.h264 else "data:video/webm;base64,"
        ) + base64.b64encode(
            Path(
                "/media-tests/red.mp4" if args.h264 else "/media-tests/red.webm"
            ).read_bytes()
        ).decode()
        video_payload = {
            **payloads[0],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What color is shown in the video? Answer with one color word.",
                        },
                        {"type": "video_url", "video_url": {"url": video}},
                    ],
                }
            ],
        }
        report["video"] = complete(video_payload)
        assert "red" in report["video"]["choices"][0]["message"]["content"].lower()
        report["after_invalid"] = complete(payloads[1])
        assert (
            "blue"
            in report["after_invalid"]["choices"][0]["message"]["content"].lower()
        )
        report["passed"] = True
    finally:
        print("REGRESSION_RESULT=" + json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
