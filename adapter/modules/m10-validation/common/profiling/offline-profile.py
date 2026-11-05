#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Measure, persist and assemble profiles through the installed production APIs."""
import argparse
import asyncio
import json
from pathlib import Path

import yaml
from dynamo.common.platform.profile import (
    validate_profile_manifest,
    write_profile_manifest,
)
from dynamo.planner.config.parallelization import PickedParallelConfig
from dynamo.profiler.interpolation import run_interpolation
from dynamo.profiler.utils.dgd_generation import assemble_final_config
from dynamo.profiler.utils.dgdr_v1beta1_types import DynamoGraphDeploymentRequestSpec
from dynamo.profiler.utils.profile_common import ProfilerOperationalConfig


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-profile", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    dgdr = DynamoGraphDeploymentRequestSpec.model_validate(data["dgdr"])
    ops = ProfilerOperationalConfig(
        output_dir=str(args.output),
        k8s_namespace=data["dgd"]["metadata"]["namespace"],
        deployment_timeout=data.get("deployment_timeout", 900),
        prefill_interpolation_granularity=5,
        decode_interpolation_granularity=3,
    )
    pick = PickedParallelConfig.model_validate(
        data.get("pick", {"tp": data.get("tp", 1)})
    )
    clients = []
    try:
        if args.reuse_profile:
            validate_profile_manifest(args.output, data["identity"])
        else:
            await run_interpolation(
                dgdr, ops, data["dgd"], pick, pick, "vllm", 4096, clients
            )
            write_profile_manifest(args.output, data["identity"])
        final = assemble_final_config(
            dgdr, ops, data["dgd"], pick, pick, resolved_backend="vllm"
        )
        name = "final_config-reused.yaml" if args.reuse_profile else "final_config.yaml"
        (args.output / name).write_text(yaml.safe_dump_all(final, sort_keys=False))
        print("OFFLINE_PROFILE_COMPLETE", flush=True)
    finally:
        for client in clients:
            await client.delete_deployment()


if __name__ == "__main__":
    asyncio.run(main())
