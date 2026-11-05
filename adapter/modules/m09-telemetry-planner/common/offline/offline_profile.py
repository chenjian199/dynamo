#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the production interpolation and artifact assembly paths on fixed PD picks.

Candidate search was tested separately; this run certifies persistence/consumption,
not optimality of the TP1 baseline. Run in the platform tools pod.
"""
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
from dynamo.profiler.utils.config_modifiers.vllm import VllmV1ConfigModifier
from dynamo.profiler.utils.dgdr_v1beta1_types import DynamoGraphDeploymentRequestSpec
from dynamo.profiler.utils.profile_common import ProfilerOperationalConfig
from dynamo.profiler.utils.profile_decode import profile_decode


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-profile", action="store_true")
    parser.add_argument("--resume-decode-url")
    parser.add_argument("--resume-max-kv-tokens", type=int)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    dgdr = DynamoGraphDeploymentRequestSpec.model_validate(data["dgdr"])
    ops = ProfilerOperationalConfig(
        output_dir=str(args.output),
        k8s_namespace="dynamo-m08-1029",
        deployment_timeout=data.get("deployment_timeout", 900),
        prefill_interpolation_granularity=5,
        decode_interpolation_granularity=3,
    )
    clients = []
    pick = PickedParallelConfig.model_validate(
        data.get("pick", {"tp": data.get("tp", 1)})
    )
    try:
        if args.reuse_profile:
            validate_profile_manifest(args.output, data["identity"])
        elif args.resume_decode_url:
            if not (
                args.output / "selected_prefill_interpolation/raw_data.npz"
            ).is_file():
                raise ValueError(
                    "Decode recovery requires the completed prefill samples"
                )
            if not args.resume_max_kv_tokens or args.resume_max_kv_tokens <= 0:
                raise ValueError(
                    "Decode recovery requires the measured engine KV capacity"
                )
            # Operator must supply the still-running sweep's endpoint and record
            # its configuration/MDC. All decode points are remeasured in this run.
            profile_decode(
                str(args.output / "selected_decode_interpolation"),
                "m09-model",
                dgdr.model,
                args.resume_decode_url,
                pick.num_gpus,
                args.resume_max_kv_tokens,
                4096,
                ops.decode_interpolation_granularity,
                pick.dp,
                max_concurrent_requests=VllmV1ConfigModifier.get_decode_max_concurrent_requests(
                    data["dgd"], pick.dp
                ),
            )
            write_profile_manifest(args.output, data["identity"])
        else:
            await run_interpolation(
                dgdr,
                ops,
                data["dgd"],
                pick,
                pick,
                "vllm",
                4096,
                clients,
            )
            write_profile_manifest(args.output, data["identity"])
        final = assemble_final_config(
            dgdr,
            ops,
            data["dgd"],
            pick,
            pick,
            resolved_backend="vllm",
        )
        (
            args.output
            / (
                "final_config-reused.yaml"
                if args.reuse_profile
                else "final_config.yaml"
            )
        ).write_text(yaml.safe_dump_all(final, sort_keys=False))
        print("OFFLINE_PROFILE_COMPLETE", flush=True)
    finally:
        for client in clients:
            await client.delete_deployment()


if __name__ == "__main__":
    asyncio.run(main())
