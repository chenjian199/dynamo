# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Do not label queued requests as active decode batches in an offline profile."""
import numpy as np
import pytest
from dynamo.profiler.utils import profile_decode
from dynamo.profiler.utils.config_modifiers.vllm import VllmV1ConfigModifier

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


@pytest.mark.parametrize("dp,limit", [(1, 16), (2, 32), (1, None)])
def test_decode_grid_respects_active_sequence_limit(tmp_path, monkeypatch, dp, limit):
    monkeypatch.setattr(profile_decode, "plot_decode_3d_surface", lambda *args: None)
    calls = []

    def measure(isl, osl, count):
        calls.append((isl, osl, count))
        return 10.0, 100.0

    profile_decode._profile_decode_helper(
        str(tmp_path), 4, 41697, 4096, 3, measure, dp, limit
    )
    assert all(count % dp == 0 for _, _, count in calls)
    assert max(count for _, _, count in calls) == (limit if limit else 69)
    with np.load(tmp_path / "raw_data.npz") as raw:
        assert len(raw["z_itl"]) == len(calls)
        assert np.allclose(
            raw["x_kv_usage"],
            [(isl + osl / 2) * count / 41697 for isl, osl, count in calls],
        )


@pytest.mark.parametrize("flag", [["--max-num-seqs", "16"], ["--max-num-seqs=16"], []])
def test_vllm_limit_parsing_and_dp_width(flag):
    config = {
        "metadata": {"name": "test"},
        "spec": {
            "components": [
                {
                    "name": "VllmDecodeWorker",
                    "type": "decode",
                    "podTemplate": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "main",
                                    "image": "vllm",
                                    "args": ["python", "-m", "dynamo.vllm", *flag],
                                }
                            ]
                        }
                    },
                }
            ]
        }
    }
    assert VllmV1ConfigModifier.get_decode_max_concurrent_requests(config, 2) == (
        32 if flag else None
    )
