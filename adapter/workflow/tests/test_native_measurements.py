# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reject a successful process whose native ownership/readback evidence is invalid."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def native_measurements(tmp_path):
    script = Path(__file__).resolve().parents[2] / (
        "modules/m06-kvbm-collectives/common/cache/native-run.py"
    )
    spec = importlib.util.spec_from_file_location("native_acceptance", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = bytes(range(256)) * 3  # outer1, width4: 3 * 4 * 16 * 4 bytes.
    for name in (
        "shard-reference",
        "shard-actual",
        "reference",
        "G2.actual",
        "G3.actual",
    ):
        (tmp_path / f"rank-0.{name}.bin").write_bytes(data)
    observation = {
        "rank": 0,
        "world": 2,
        "device": 3,
        "outer": 1,
        "width": 4,
        "completed_operations": 12,
        "sdk_create_release_counts": [10, 10, 0, 0, 5, 5, 12, 12],
        "metrics": {"live_allocations": 0},
        "layout": {
            "num_layers": 3,
            "num_blocks": 4,
            "page_size": 4,
            "num_heads": 2,
            "inner_dim": 16,
            "dtype_width_bytes": 4,
        },
        "shard_bytes": len(data),
        "tier_readback_bytes": [len(data), len(data)],
    }
    path = tmp_path / "rank-0.measurements.json"
    path.write_text(json.dumps(observation))
    return module, tmp_path, path, observation


def test_native_measurement_recomputes_readback_hashes(native_measurements):
    module, case, _, _ = native_measurements
    result = module.verify_rank(case, 0, 2, "3", 1, 4)
    assert (
        result["sha256"]["rank-0.G2.actual.bin"]
        == result["sha256"]["rank-0.reference.bin"]
    )
    (case / "rank-0.G2.actual.bin").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="readback parity"):
        module.verify_rank(case, 0, 2, "3", 1, 4)


@pytest.mark.parametrize("failure", ["allocation", "stream", "wrong_device", "missing"])
def test_pass_text_does_not_replace_native_measurements(native_measurements, failure):
    module, case, path, observation = native_measurements
    (case / "rank-0.log.txt").write_text("passed=true\n")
    if failure == "missing":
        path.unlink()
    else:
        if failure == "allocation":
            observation["sdk_create_release_counts"][1] -= 1
        elif failure == "stream":
            observation["sdk_create_release_counts"][5] -= 1
        else:
            observation["device"] = 0
        path.write_text(json.dumps(observation))
    with pytest.raises((ValueError, FileNotFoundError)):
        module.verify_rank(case, 0, 2, "3", 1, 4)
