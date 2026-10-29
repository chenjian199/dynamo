#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Native vLLM baseline on allocated devices, compared with frozen Dynamo responses."""

import argparse
import json
import time
from pathlib import Path

from vllm import LLM, SamplingParams


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", required=True)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--tp", type=int, default=2)
    p.add_argument("--block-size", type=int, required=True)
    args = p.parse_args()
    reference = json.loads(args.reference.read_text())
    llm = LLM(
        model=args.model,
        enforce_eager=True,
        tensor_parallel_size=args.tp,
        max_model_len=4096,
        max_num_seqs=4,
        kv_cache_memory_bytes=1073741824,
        block_size=args.block_size,
        enable_prefix_caching=True,
    )
    sampling = SamplingParams(temperature=0, seed=42, max_tokens=16)
    prompts = [
        "The capital of France is",
        "This is a stable prefix for cache routing. " * 128
        + "The capital of France is",
    ]
    expected = [
        reference["requests"][0]["response"]["choices"][0]["text"],
        reference["long_prefix"][0]["choices"][0]["text"],
    ]
    rows = []
    for prompt, text in zip(prompts, expected):
        for repetition in range(3):
            start = time.monotonic()
            output = llm.generate([prompt], sampling)[0].outputs[0]
            rows.append(
                {
                    "repetition": repetition,
                    "text": output.text,
                    "token_ids": output.token_ids,
                    "seconds": time.monotonic() - start,
                    "matches_dynamo": output.text == text,
                }
            )
    passed = all(row["matches_dynamo"] for row in rows)
    print(
        "REGRESSION_RESULT="
        + json.dumps(
            {
                "passed": passed,
                "cases": rows,
                "qualification": "correctness comparison; concurrent cluster load excludes isolated performance claims",
            }
        ),
        flush=True,
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
