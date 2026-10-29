#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Frozen Ascend DeepSeek engine-only repeatability control (no Dynamo)."""

import importlib.metadata
import json

from vllm import LLM, SamplingParams


def main():
    llm = LLM(
        model="/models/DeepSeek-V4-Flash-w8a8-mtp",
        tensor_parallel_size=8,
        enable_expert_parallel=True,
        enforce_eager=True,
        max_model_len=4096,
        max_num_batched_tokens=4096,
        max_num_seqs=4,
        gpu_memory_utilization=0.9,
        block_size=128,
        tokenizer_mode="deepseek_v4",
        quantization="ascend",
        enable_prefix_caching=False,
        additional_config={"enable_dsa_cp": True},
    )
    sampling = SamplingParams(temperature=0, seed=42, max_tokens=16)
    rows = []
    for batch in (1, 1, 1, 1, 1, 1, 4, 4, 4, 4):
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
                "versions": {
                    n: importlib.metadata.version(n)
                    for n in ("vllm", "vllm-ascend", "torch-npu")
                },
                "qualification": "Native LLM diagnostic without Dynamo; not an identical API scheduling claim",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
