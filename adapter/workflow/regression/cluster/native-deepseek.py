#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compare single/batched greedy output in the frozen native DeepSeek engine."""

import json

from vllm import LLM, SamplingParams


def main():
    llm = LLM(
        model="/models/DeepSeek-V4-Flash-0731",
        tensor_parallel_size=4,
        enforce_eager=True,
        max_model_len=4096,
        max_num_seqs=4,
        kv_cache_memory_bytes=4294967296,
        block_size=256,
        kv_cache_dtype="fp8",
        tokenizer_mode="deepseek_v4",
        enable_prefix_caching=False,
        enable_flashinfer_autotune=False,
    )
    sampling = SamplingParams(temperature=0, seed=42, max_tokens=16)
    rows = []
    for batch in (1, 1, 1, 4, 4, 4, 4):
        outputs = llm.generate(["The capital of France is"] * batch, sampling)
        rows.append(
            {
                "batch": batch,
                "outputs": [
                    {"text": r.outputs[0].text, "token_ids": r.outputs[0].token_ids}
                    for r in outputs
                ],
            }
        )
    unique = {tuple(x["token_ids"]) for r in rows for x in r["outputs"]}
    print(
        "REGRESSION_RESULT="
        + json.dumps(
            {
                "passed": len(unique) == 1,
                "unique_outputs": len(unique),
                "cases": rows,
                "qualification": "Native engine batch-repeatability diagnostic; does not assert identical native/API scheduling.",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
