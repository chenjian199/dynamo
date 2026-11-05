#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise hybrid PD block boundaries, concurrent streams and cancellation.

Synthetic token inputs validate transfer/lifecycle behavior, not model quality.
Use the same token-accounting implementation as the M09/M10 load campaign.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import aiohttp


async def main(args):
    spec = importlib.util.spec_from_file_location("m10_load", args.load_script)
    load = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(load)
    report = {"passed": False, "requests": [], "cancellations": 0}
    options = SimpleNamespace(
        url=args.url,
        model=args.model,
        isl=128,
        osl=16,
        token_id_metrics=True,
        record_token_ids=True,
    )
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=180)
        ) as session:
            for index, length in enumerate((1, 127, 128, 129, 512, 1024, 2048)):
                options.isl = length
                record = await load.request(session, options, index)
                assert record["prompt_tokens"] == length, record
                assert record["completion_tokens"] == options.osl, record
                report["requests"].append(record)
                print("BOUNDARY=" + json.dumps(record), flush=True)
            options.isl = 512
            for start in (20, 22):
                records = await asyncio.gather(
                    *(
                        load.request(session, options, i)
                        for i in range(start, start + 2)
                    )
                )
                report["requests"].extend(records)
            for index in range(4):
                payload = {
                    "model": args.model,
                    "prompt": [1100 + index] * 512,
                    "max_tokens": 128,
                    "temperature": 0,
                    "ignore_eos": True,
                    "stream": True,
                    "nvext": {"extra_fields": ["completion_token_ids"]},
                }
                async with session.post(
                    args.url.rstrip("/") + "/v1/completions", json=payload
                ) as response:
                    assert response.status == 200, await response.text()
                    async for raw in response.content:
                        if not raw.startswith(b"data:"):
                            continue
                        body = raw[5:].strip()
                        if body == b"[DONE]":
                            raise AssertionError("Request finished before cancellation")
                        event = json.loads(body)
                        assert not event.get("error"), event
                        if (event.get("nvext") or {}).get("completion_token_ids"):
                            break
                    else:
                        raise AssertionError("No token arrived before cancellation")
                report["cancellations"] += 1
                await asyncio.sleep(1)
            report["recovery"] = await load.request(session, options, 50)
            report["passed"] = True
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print("HYBRID_PD_RESULT=" + json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", default="m09-model")
    parser.add_argument("--load-script", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
