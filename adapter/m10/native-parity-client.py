#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Record repeatability of native vLLM token outputs without Dynamo or KV transfer."""
import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp


async def main(args):
    report = {
        "requests_succeeded": False,
        "transport": "native-vllm-http-without-kv-connector",
        "rounds": [],
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=180)
        ) as session:

            async def request(sequence, length):
                payload = {
                    "model": args.model,
                    "prompt": [1000 + sequence % 100] * length,
                    "max_tokens": 16,
                    "temperature": 0,
                    "ignore_eos": True,
                    "return_token_ids": True,
                    "stream": False,
                }
                start = time.monotonic()
                async with session.post(
                    args.url.rstrip("/") + "/v1/completions", json=payload
                ) as response:
                    body = await response.json()
                    assert response.status == 200, body
                tokens = body["choices"][0]["token_ids"]
                assert len(tokens) == body["usage"]["completion_tokens"] == 16
                assert body["usage"]["prompt_tokens"] == length
                return {
                    "sequence": sequence,
                    "prompt_tokens": length,
                    "completion_token_ids": tokens,
                    "elapsed": time.monotonic() - start,
                    "response": body,
                }

            for index in range(args.rounds):
                rows = []
                for sequence, length in enumerate((1, 127, 128, 129, 512, 1024, 2048)):
                    rows.append(await request(sequence, length))
                for start in (20, 22):
                    rows.extend(
                        await asyncio.gather(
                            *(
                                request(sequence, 512)
                                for sequence in range(start, start + 2)
                            )
                        )
                    )
                rows.append(await request(50, 512))
                report["rounds"].append(rows)
                print(
                    "ROUND=" + json.dumps({"index": index, "requests": rows}),
                    flush=True,
                )
        reference = {
            row["sequence"]: row["completion_token_ids"] for row in report["rounds"][0]
        }
        report["differences_from_first_round"] = [
            {
                "round": index,
                "sequence": row["sequence"],
                "tokens": row["completion_token_ids"],
            }
            for index, rows in enumerate(report["rounds"][1:], 1)
            for row in rows
            if row["completion_token_ids"] != reference[row["sequence"]]
        ]
        report["repeatable"] = not report["differences_from_first_round"]
        report["requests_succeeded"] = True
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {key: value for key, value in report.items() if key != "rounds"}
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", default="m09-model")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 2:
        parser.error("At least two rounds are required to check repeatability")
    asyncio.run(main(args))
