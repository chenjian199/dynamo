#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prove file bootstrap can initialize predictive models without live FPMs."""
import argparse
import asyncio
import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import yaml
from dynamo.common.platform.profile import ProfileIdentity, validate_profile_manifest
from dynamo.planner.config.defaults import SubComponentType
from dynamo.planner.config.planner_config import PlannerConfig
from dynamo.planner.core.perf_model.aic_adapter import PlannerEnginePerfModel
from dynamo.planner.core.types import EngineCapabilities
from dynamo.planner.monitoring.perf_metrics import fetch_pre_deployment_metrics
from dynamo.profiler.utils.profile_decode import get_num_request_range


async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--assembly", default="final_config.yaml")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mdc", type=Path, required=True)
    p.add_argument("--prefill-mdc", type=Path)
    a = p.parse_args()
    objs = list(yaml.safe_load_all((a.profile / a.assembly).read_text()))
    config = next(
        json.loads(o["data"]["planner_config.json"])
        for o in objs
        if o.get("kind") == "ConfigMap" and "planner_config.json" in o.get("data", {})
    )
    data = next(
        o["data"]
        for o in objs
        if o.get("kind") == "ConfigMap" and "profile-identity.json" in o.get("data", {})
    )
    mounted = a.profile / "verification-mounted"
    mounted.mkdir(exist_ok=True)
    for name, content in data.items():
        (mounted / name).write_text(content)
    identity = ProfileIdentity.model_validate(config["profile_identity"])
    manifest = validate_profile_manifest(mounted, identity)
    config.update(profile_results_dir=str(mounted), load_min_observations=3)
    config = PlannerConfig.model_validate(config)
    # This campaign freezes the production sweep at context=4096 and granularity
    # 5/3. Reject a partial NPZ even if every sample it does contain is finite.
    with np.load(a.profile / "selected_prefill_interpolation/raw_data.npz") as raw:
        expected_isl = list(range(100, 4096 - 512, (4096 - 512 - 100) // 5))
        if list(raw["prefill_isl"]) != expected_isl:
            raise ValueError("Prefill interpolation coverage is incomplete")
    with np.load(a.profile / "selected_decode_interpolation/raw_data.npz") as raw:
        measured_capacity = int(raw["max_kv_tokens"][0])
        max_concurrent = (
            int(raw["max_concurrent_requests"][0])
            if "max_concurrent_requests" in raw
            else 0
        )
        expected_points = [
            ((isl + 250) * n / measured_capacity, isl + 250)
            for isl in range(100, 4096 - 500, (4096 - 500) // 3)
            for n in get_num_request_range(
                1,
                (
                    min(measured_capacity // (isl + 500), max_concurrent)
                    if max_concurrent
                    else measured_capacity // (isl + 500)
                ),
                3,
            )
        ]
        actual = list(zip(raw["x_kv_usage"], raw["y_context_length"]))
        if len(actual) != len(expected_points) or not np.allclose(
            actual, expected_points, rtol=1e-9, atol=1e-9
        ):
            raise ValueError("Decode interpolation coverage is incomplete")
    block_size = identity.kv_config.get("block_size", 128)
    arguments = identity.kv_config.get("worker_arguments", [])
    if "--block-size" in arguments:
        block_size = int(arguments[arguments.index("--block-size") + 1])

    def runtimes_in(value):
        found = []
        if isinstance(value, dict):
            if "runtime_config" in value:
                found.append(value["runtime_config"])
            for child in value.values():
                found.extend(runtimes_in(child))
        elif isinstance(value, list):
            for child in value:
                found.extend(runtimes_in(child))
        return found

    reports = []
    for role in [SubComponentType.PREFILL, SubComponentType.DECODE]:
        metadata_path = (
            a.prefill_mdc
            if role == SubComponentType.PREFILL and a.prefill_mdc
            else a.mdc
        )
        runtimes = runtimes_in(json.loads(metadata_path.read_text()))
        if len(runtimes) != 1:
            raise ValueError(
                "Supply a sweep MDC snapshot containing exactly one engine"
            )
        runtime = runtimes[0]
        engine = runtime["runtime_data"]["platform_engine"]
        caps = EngineCapabilities(
            num_gpu=engine["tensor_parallel_size"],
            max_num_batched_tokens=runtime["max_num_batched_tokens"],
            max_num_seqs=runtime["max_num_seqs"],
            context_length=runtime["context_length"],
            max_kv_tokens=runtime["runtime_data"]["kv_cache_capacity"]["tokens"],
            kv_cache_block_size=block_size,
        )
        if role == SubComponentType.DECODE:
            sampled_peak = max(
                round(usage * measured_capacity / context)
                for usage, context in expected_points
            )
            if sampled_peak > caps.max_num_seqs:
                raise ValueError(
                    "Decode profile labels queued requests as active sequences"
                )
        samples = await fetch_pre_deployment_metrics(
            None,
            "unused",
            None,
            str(mounted),
            role,
            profile_identity=identity,
            source="files",
        )
        predictions = []
        for _restart in range(2):
            model = PlannerEnginePerfModel(
                worker_type=role.value, config=config, capabilities=caps
            )
            model.load_benchmark_fpms(samples)
            capacity = model.find_engine_capacity_rps(
                isl=512,
                osl=128,
                ttft_sla_ms=(
                    config.ttft_ms if role == SubComponentType.PREFILL else None
                ),
                itl_sla_ms=(config.itl_ms if role == SubComponentType.DECODE else None),
            )
            predictions.append(dataclasses.asdict(capacity) if capacity else None)
        reports.append(
            {
                "role": role.value,
                "capabilities": dataclasses.asdict(caps),
                "capabilities_source": str(metadata_path),
                "samples": len(samples),
                "predictions": predictions,
                "reusable": predictions[0] is not None
                and predictions[1] is not None
                and all(
                    (
                        math.isclose(
                            value, predictions[1][key], rel_tol=1e-9, abs_tol=1e-9
                        )
                        if isinstance(value, float)
                        else value == predictions[1][key]
                    )
                    for key, value in predictions[0].items()
                ),
            }
        )
    result = {
        "source": "files only; no live worker access",
        "restart_prediction_tolerance": {"relative": 1e-9, "absolute": 1e-9},
        "fingerprint": manifest["fingerprint"],
        "coverage": {"prefill": len(expected_isl), "decode": len(expected_points)},
        "models": reports,
        "passed": all(x["reusable"] for x in reports),
    }
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
