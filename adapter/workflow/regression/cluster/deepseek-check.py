#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded DeepSeek concurrency/length checks, with determinism reported separately."""

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--model", required=True)
    args = p.parse_args()
    report = {"passed": False, "cases": [], "chat": None}

    def request(payload, chat=False):
        start = time.monotonic()
        req = urllib.request.Request(
            args.url + ("/v1/chat/completions" if chat else "/v1/completions"),
            data=json.dumps(
                {
                    "model": args.model,
                    "temperature": 0,
                    "seed": 42,
                    "max_tokens": 64,
                    **payload,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.load(response)
        assert result["usage"]["completion_tokens"] > 0, result
        assert result["choices"], result
        return {"seconds": time.monotonic() - start, "response": result}

    try:
        payload = {"prompt": "The capital of France is", "max_tokens": 16}
        reference = request(payload)
        req = urllib.request.Request(
            args.url + "/v1/completions",
            data=json.dumps(
                {
                    "model": args.model,
                    "temperature": 0,
                    "seed": 42,
                    **payload,
                    "stream": True,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        pieces, done = [], False
        with urllib.request.urlopen(req, timeout=300) as response:
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
        assert done and "".join(pieces), "incomplete SSE"
        report["sse"] = {
            "done": done,
            "text": "".join(pieces),
            "reference": reference,
            "parity": "".join(pieces) == reference["response"]["choices"][0]["text"],
        }
        for _ in range(4):
            req.data = json.dumps(
                {
                    "model": args.model,
                    "temperature": 0,
                    "seed": 42,
                    **payload,
                    "max_tokens": 1024,
                    "stream": True,
                }
            ).encode()
            with urllib.request.urlopen(req, timeout=300) as response:
                for line in response:
                    if line.startswith(b"data:") and b"[DONE]" not in line:
                        event = json.loads(line[5:])
                        if event.get("choices") and event["choices"][0].get("text"):
                            break
                else:
                    raise AssertionError("no token before cancellation")
            time.sleep(1)
        report["cancellations"] = 4
        report["post_cancel_recovery"] = request(payload)
        for length in (32, 2048):
            prompt = [1000, 1001] * (length // 2)
            for concurrency in (1, 4, 8):
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    rows = list(
                        pool.map(
                            lambda _: request({"prompt": prompt}),
                            range(max(4, concurrency)),
                        )
                    )
                texts = [r["response"]["choices"][0]["text"] for r in rows]
                assert all(texts), "empty completion"
                assert all(
                    r["response"]["usage"]["prompt_tokens"] == length for r in rows
                ), "input token accounting"
                report["cases"].append(
                    {
                        "input_tokens": length,
                        "concurrency": concurrency,
                        "unique_outputs": len(set(texts)),
                        "responses": rows,
                    }
                )
                print(
                    json.dumps(
                        {
                            "input_tokens": length,
                            "concurrency": concurrency,
                            "unique_outputs": len(set(texts)),
                        }
                    ),
                    flush=True,
                )
        report["chat"] = request(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "What is 2 + 2? Reply with the number only.",
                    }
                ],
                "max_tokens": 256,
                "chat_template_kwargs": {"thinking": False},
            },
            chat=True,
        )
        message = report["chat"]["response"]["choices"][0]["message"]
        assert message.get("content"), "no final chat content"
        assert "4" in message["content"], "arithmetic smoke answer missing"
        report.update(
            passed=True,
            ladder_deterministic=all(c["unique_outputs"] == 1 for c in report["cases"]),
            qualification="API/queue/length smoke only; determinism separately reported",
        )
    except Exception as exc:
        report["error"] = repr(exc)
        raise
    finally:
        print("REGRESSION_RESULT=" + json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
