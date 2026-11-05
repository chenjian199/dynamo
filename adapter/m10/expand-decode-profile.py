#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Freeze a separate wider-batch profile input without altering failed samples."""
import argparse
import copy
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--max-num-seqs", type=int, default=64)
    args = parser.parse_args()
    source = args.input_directory / "input.json"
    data = json.loads(source.read_text())
    original_identity = copy.deepcopy(data["identity"])
    changes = []

    def widen(arguments, label):
        index = arguments.index("--max-num-seqs") + 1
        previous = int(arguments[index])
        if args.max_num_seqs <= previous:
            raise ValueError("New batch limit must exceed the measured limit")
        arguments[index] = str(args.max_num_seqs)
        changes.append(
            {"target": label, "before": previous, "after": args.max_num_seqs}
        )

    for component in data["dgd"]["spec"]["components"]:
        if component["type"] in ("prefill", "decode"):
            widen(
                component["podTemplate"]["spec"]["containers"][0]["args"],
                component["type"],
            )
    if len(changes) != 2:
        raise ValueError("Expected one prefill and one decode component")
    widen(data["identity"]["kv_config"]["worker_arguments"], "profile_identity")
    if len({item["before"] for item in changes}) != 1:
        raise ValueError("Worker limits and original profile identity disagree")
    planner = data["dgdr"]["features"]["planner"]
    planner["profile_identity"] = copy.deepcopy(data["identity"])
    data["dgdr"]["hardware"]["totalGpus"] = planner["max_gpu_budget"]
    data["dgd"]["metadata"]["name"] = args.name
    tools = json.loads((args.input_directory / "tools.json").read_text())
    tools["metadata"]["name"] = args.name + "-tools"
    contract = {
        "source_input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "reason": "The complete narrow-batch sweep could not initialize native Decode regression",
        "changes": changes,
        "original_identity": original_identity,
        "new_identity": data["identity"],
        "prefill_granularity": 5,
        "decode_granularity": 3,
        "max_context_length": 4096,
        "policy": "One complete new production sweep; no sample selection, blending, or readiness override. Preserve the original failed sweep. New results apply only to the new configuration identity.",
    }
    args.output.mkdir(parents=True, exist_ok=False)
    for name, value in (
        ("input.json", data),
        ("tools.json", tools),
        ("sampling-contract.json", contract),
    ):
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")
    (args.output / "rbac.json").write_bytes(
        (args.input_directory / "rbac.json").read_bytes()
    )


if __name__ == "__main__":
    main()
