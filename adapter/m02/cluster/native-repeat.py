#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Isolate greedy repeatability in the frozen native engine, without Dynamo."""

import json

from vllm import LLM, SamplingParams


def main():
    llm = LLM(
        model="/models/Qwen3-32B",
        enforce_eager=True,
        tensor_parallel_size=4,
        max_model_len=4096,
        max_num_seqs=4,
        kv_cache_memory_bytes=1073741824,
        block_size=128,
        enable_prefix_caching=True,
    )
    sampling = SamplingParams(temperature=0, seed=42, max_tokens=16)
    rows = []
    for index in range(16):
        output = llm.generate(["The capital of France is"], sampling)[0].outputs[0]
        rows.append({"index": index, "text": output.text, "tokens": output.token_ids})
    unique = {tuple(r["tokens"]) for r in rows}
    print(
        "REGRESSION_RESULT="
        + json.dumps(
            {
                "passed": len(unique) == 1,
                "unique_outputs": len(unique),
                "cases": rows,
                "qualification": "native vLLM TP4 repeatability, no Dynamo frontend or worker",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
