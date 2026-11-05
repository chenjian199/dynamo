#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Correct inherited GPU-budget metadata without changing measured engine inputs."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from dynamo.planner.config.parallelization import PickedParallelConfig
from dynamo.profiler.utils.dgd_generation import _build_planner_config
from dynamo.profiler.utils.dgdr_v1beta1_types import DynamoGraphDeploymentRequestSpec


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
original = json.loads(args.input.read_text())
corrected = copy.deepcopy(original)
pick = PickedParallelConfig.model_validate(original["pick"])
budget = 2 * pick.num_gpus
assert original["dgdr"]["features"]["planner"]["max_gpu_budget"] == budget
corrected["dgdr"]["hardware"]["totalGpus"] = budget
before = _build_planner_config(
    DynamoGraphDeploymentRequestSpec.model_validate(original["dgdr"]), pick, pick
).model_dump(mode="json")
after = _build_planner_config(
    DynamoGraphDeploymentRequestSpec.model_validate(corrected["dgdr"]), pick, pick
).model_dump(mode="json")
assert before == after
assert original["identity"] == corrected["identity"]
assert original["dgd"] == corrected["dgd"]
assert original["pick"] == corrected["pick"]
(args.output / "input-measured.json").write_bytes(args.input.read_bytes())
(args.output / "input-delivery.json").write_text(json.dumps(corrected, indent=2) + "\n")
(args.output / "planner-config.json").write_text(json.dumps(after, indent=2) + "\n")
report = {
    "passed": True,
    "change": {
        "path": "dgdr.hardware.totalGpus",
        "before": original["dgdr"]["hardware"]["totalGpus"],
        "after": budget,
    },
    "engine_dgd_unchanged": True,
    "picked_parallel_config_unchanged": True,
    "profile_identity_unchanged": True,
    "generated_planner_config_unchanged": True,
    "scope": "Fixed-pick interpolation and Planner configuration; not a DGDR candidate search rerun",
    "evidence": {
        name: sha(args.output / name)
        for name in [
            "input-measured.json",
            "input-delivery.json",
            "planner-config.json",
        ]
    },
}
(args.output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
