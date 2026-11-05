#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pair native local and Hybrid PD requests on the same decode engine.

Uses the installed vLLM-Ascend example proxy's handshake: prefill one token,
forward its KV metadata, and preserve the original prompt. No Dynamo endpoint
or implementation overlay participates. Differences are findings, not passes.
"""
import argparse
import asyncio
import json
from pathlib import Path

import aiohttp


async def main(args):
    report = {
        "requests_succeeded": False,
        "transport": "native-vllm-http-hybrid-pd-without-dynamo",
        "rounds": [],
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=180)
        ) as session:

            async def post(url, payload):
                async with session.post(
                    url.rstrip("/") + "/v1/completions", json=payload
                ) as response:
                    body = await response.json()
                    assert response.status == 200, body
                    return body

            async def paired(sequence, length):
                payload = {
                    "model": args.model,
                    "prompt": [1000 + sequence % 100] * length,
                    "max_tokens": 16,
                    "temperature": 0,
                    "ignore_eos": True,
                    "return_token_ids": True,
                    "stream": False,
                }
                before = await post(args.decode_url, payload)
                prefill_payload = {
                    **payload,
                    "max_tokens": 1,
                    "min_tokens": 1,
                    "kv_transfer_params": {
                        "do_remote_decode": True,
                        "do_remote_prefill": False,
                        "remote_engine_id": None,
                        "remote_block_ids": None,
                        "remote_host": None,
                        "remote_port": None,
                    },
                }
                prefill = await post(args.prefill_url, prefill_payload)
                params = prefill["kv_transfer_params"]
                assert params["do_remote_prefill"] is True
                assert isinstance(params["remote_block_ids"], list)
                pd = await post(
                    args.decode_url, {**payload, "kv_transfer_params": params}
                )
                after = await post(args.decode_url, payload)
                tokens = {}
                for name, response in [
                    ("local_before", before),
                    ("pd", pd),
                    ("local_after", after),
                ]:
                    tokens[name] = response["choices"][0]["token_ids"]
                    assert (
                        len(tokens[name])
                        == response["usage"]["completion_tokens"]
                        == 16
                    )
                    assert response["usage"]["prompt_tokens"] == length
                return {
                    "sequence": sequence,
                    "prompt_tokens": length,
                    "tokens": tokens,
                    "all_equal": tokens["local_before"]
                    == tokens["pd"]
                    == tokens["local_after"],
                    "local_repeatable": tokens["local_before"] == tokens["local_after"],
                    "responses": {
                        "local_before": before,
                        "prefill": prefill,
                        "pd": pd,
                        "local_after": after,
                    },
                }

            for index in range(args.rounds):
                rows = []
                for sequence, length in enumerate((1, 127, 128, 129, 512, 1024, 2048)):
                    rows.append(await paired(sequence, length))
                for start in (20, 22):
                    rows.extend(
                        await asyncio.gather(
                            *(paired(i, 512) for i in range(start, start + 2))
                        )
                    )
                rows.append(await paired(50, 512))
                report["rounds"].append(rows)
                print(
                    "ROUND=" + json.dumps({"index": index, "requests": rows}),
                    flush=True,
                )
            report["differences"] = [
                {
                    "round": index,
                    **{
                        key: row[key]
                        for key in [
                            "sequence",
                            "prompt_tokens",
                            "tokens",
                            "local_repeatable",
                        ]
                    },
                }
                for index, rows in enumerate(report["rounds"])
                for row in rows
                if not row["all_equal"]
            ]
            report["pairwise_exact"] = not report["differences"]
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
    parser.add_argument("--prefill-url", required=True)
    parser.add_argument("--decode-url", required=True)
    parser.add_argument("--model", default="m09-model")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("At least one round is required")
    asyncio.run(main(args))
