#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Repeat every prefill point with a fixed sample count; retain the failed sweep."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from dynamo.common.platform.profile import (
    validate_profile_manifest,
    write_profile_manifest,
)
from dynamo.profiler.utils.aiperf import benchmark_prefill
from dynamo.profiler.utils.profile_prefill import _profile_prefill_helper

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--input", type=Path, required=True)
p.add_argument("--source-profile", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--url", required=True)
p.add_argument("--requests-per-point", type=int, default=16)
a = p.parse_args()
if a.requests_per_point < 2:
    p.error("Repeated sampling requires at least two requests per point")
data = json.loads(a.input.read_text())
manifest = validate_profile_manifest(a.source_profile, data["identity"])
a.output.mkdir(exist_ok=False)
shutil.copytree(
    a.source_profile / "selected_decode_interpolation",
    a.output / "selected_decode_interpolation",
)
work = a.output / "selected_prefill_interpolation"
work.mkdir()
shutil.copyfile(
    a.source_profile / "selected_prefill_interpolation/config.yaml",
    work / "config.yaml",
)
contract = {
    "reason": "Single-request prefill sweep has insufficient positive load signal for native regression",
    "source_fingerprint": manifest["fingerprint"],
    "source_profile": str(a.source_profile),
    "source_prefill_sha256": hashlib.sha256(
        (a.source_profile / "selected_prefill_interpolation/raw_data.npz").read_bytes()
    ).hexdigest(),
    "concurrency": 1,
    "requests_per_point": a.requests_per_point,
    "warmup_requests_per_point": 8,
    "grid": [100, 796, 1492, 2188, 2884, 3580],
    "policy": "Measure all points once; no selection by slope or latency; reuse unchanged completed decode samples",
}
(a.output / "repeat-contract.json").write_text(json.dumps(contract, indent=2) + "\n")


def measure(isl):
    result = benchmark_prefill(
        isl,
        str(work / f"aiperf_isl{isl}"),
        "m09-model",
        data["dgdr"]["model"],
        base_url=a.url,
        concurrency=1,
        request_count=a.requests_per_point,
        warmup_request_count=8,
    )
    if result is None or not result["is_complete"] or result["error_summary"]:
        raise RuntimeError(f"Incomplete prefill measurement at ISL {isl}")
    return float(result["time_to_first_token"]["avg"])


_profile_prefill_helper(str(work), data["pick"]["tp"], 4096, 5, measure)
write_profile_manifest(a.output, data["identity"], now=manifest["created_at"])
print("REPEATED_PREFILL_COMPLETE", flush=True)
