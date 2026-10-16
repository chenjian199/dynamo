#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Create a deterministic per-channel W8A8 dynamic checkpoint for loading tests.

No calibration/quality claim: this validates the vendor INT8 execution path.
Uses the installed compressed-tensors serializer and quantizer on CPU.
"""

import argparse
import json
import shutil
from pathlib import Path

import torch
from compressed_tensors.compressors.naive_quantized import IntQuantizationCompressor
from compressed_tensors.quantization import (
    QuantizationArgs,
    QuantizationConfig,
    QuantizationScheme,
)
from safetensors.torch import load_file, save_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), "Use a new output directory"
    torch.set_num_threads(8)
    args.output.mkdir(parents=True)
    scheme = QuantizationScheme(
        targets=["Linear"],
        weights=QuantizationArgs(
            num_bits=8, type="int", strategy="channel", symmetric=True, dynamic=False
        ),
        input_activations=QuantizationArgs(
            num_bits=8, type="int", strategy="token", symmetric=True, dynamic=True
        ),
    )
    for source in args.model.iterdir():
        if (
            source.suffix in (".json", ".txt", ".jinja", ".model")
            and source.name != "model.safetensors.index.json"
        ):
            shutil.copyfile(source, args.output / source.name)
    tensors = {}
    for source in sorted(args.model.glob("*.safetensors")):
        for name, weight in load_file(source).items():
            if name.startswith("model.layers.") and name.endswith("_proj.weight"):
                scale = (
                    weight.float().abs().amax(dim=1, keepdim=True).clamp(min=1e-10)
                    / 127
                )
                compressed = IntQuantizationCompressor.compress(
                    {"weight": weight.float(), "weight_scale": scale}, scheme
                )
                prefix = name.removesuffix("weight")
                tensors.update(
                    {prefix + key: value for key, value in compressed.items()}
                )
            else:
                tensors[name] = weight
    save_file(
        tensors, str(args.output / "model.safetensors"), metadata={"format": "pt"}
    )
    config = json.loads((args.model / "config.json").read_text())
    quant = QuantizationConfig(
        config_groups={"group_0": scheme},
        ignore=["lm_head"],
        quantization_status="compressed",
        format="int-quantized",
    )
    config["quantization_config"] = json.loads(quant.model_dump_json())
    (args.output / "config.json").write_text(json.dumps(config, indent=2) + "\n")


if __name__ == "__main__":
    main()
