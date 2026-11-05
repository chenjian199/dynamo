#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Create a nonzero deterministic Qwen3 adapter for lifecycle tests, not model quality."""

import argparse
import json
from pathlib import Path

import torch
from safetensors.torch import save_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.model / "config.json").read_text())
    assert config["model_type"] == "qwen3", "This fixture is specific to Qwen3"
    args.output.mkdir(parents=True, exist_ok=True)
    rank = 8
    generator = torch.Generator().manual_seed(20261016)
    tensors = {}
    for layer in range(config["num_hidden_layers"]):
        for module, heads in (
            ("q_proj", config["num_attention_heads"]),
            ("v_proj", config["num_key_value_heads"]),
        ):
            prefix = f"base_model.model.model.layers.{layer}.self_attn.{module}"
            tensors[prefix + ".lora_A.weight"] = (
                torch.randn(rank, config["hidden_size"], generator=generator) * 0.01
            ).to(torch.bfloat16)
            tensors[prefix + ".lora_B.weight"] = (
                torch.randn(heads * config["head_dim"], rank, generator=generator)
                * 0.01
            ).to(torch.bfloat16)
    save_file(tensors, str(args.output / "adapter_model.safetensors"))
    (args.output / "adapter_config.json").write_text(
        json.dumps(
            dict(
                base_model_name_or_path=str(args.model),
                peft_type="LORA",
                task_type="CAUSAL_LM",
                inference_mode=True,
                r=rank,
                lora_alpha=16,
                lora_dropout=0.0,
                bias="none",
                target_modules=["q_proj", "v_proj"],
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
