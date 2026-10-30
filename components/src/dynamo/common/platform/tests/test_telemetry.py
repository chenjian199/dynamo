# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import math

import pytest
from dynamo.common.platform.telemetry import normalize_exporter, observation

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


@pytest.mark.parametrize(
    "value,timestamp,state",
    [
        (0, 100, "available"),
        (None, None, "unavailable"),
        (1, 10, "stale"),
        (math.nan, 100, "error"),
        (2**63, 100, "error"),
        (1, 200, "error"),
        (1, math.nan, "error"),
    ],
)
def test_availability_does_not_confuse_zero(value, timestamp, state):
    result = observation(
        value, unit="W", sampled_at=timestamp, collected_at=100, source="test"
    )
    assert result.state == state


def test_diagnostic_dump_prefers_explicit_exporter_and_survives_sdk_failure(
    monkeypatch,
):
    from dynamo.common.config_dump import system_info

    monkeypatch.setenv("DYN_PLATFORM", "ascend")
    monkeypatch.setenv("DYN_TELEMETRY_EXPORTER_URL", "http://exporter/metrics")
    monkeypatch.setattr(
        system_info,
        "collect_exporter",
        lambda url, platform: {"platform": platform, "url": url},
    )
    assert system_info.get_gpu_info()["platform"] == "ascend"

    def invalid_exporter(url, platform):
        raise ValueError("ambiguous device identity")

    monkeypatch.setattr(system_info, "collect_exporter", invalid_exporter)
    assert system_info.get_gpu_info()["state"] == "unavailable"
    monkeypatch.delenv("DYN_TELEMETRY_EXPORTER_URL")

    def failed_query(platform):
        raise RuntimeError("device memory query failed")

    monkeypatch.setattr(system_info, "collect_local", failed_query)
    assert system_info.get_gpu_info()["state"] == "unavailable"


def test_ascend_timestamp_and_health_mapping():
    text = "\n".join(
        f'{name}{{vdie_id="die-1"}} {value} 100000'
        for name, value in [
            ("npu_chip_info_hbm_total_memory", 65536),
            ("npu_chip_info_hbm_used_memory", 1024),
            ("npu_chip_info_health_status", 1),
            ("npu_chip_info_utilization", 0),
        ]
    )
    device = normalize_exporter(text, "ascend", collected_at=101)["devices"][0]
    assert device["metrics"]["memory.available"]["value"] == (65536 - 1024) * 2**20
    assert device["metrics"]["utilization"]["value"] == 0
    assert device["metrics"]["power.measured"]["state"] == "unavailable"
    assert device["health"]["severity"] == "ok"
    stale = normalize_exporter(text, "ascend", collected_at=200)["devices"][0]
    assert stale["health"]["severity"] == "unknown"


def test_cuda_xid_zero_not_overall_health():
    device = normalize_exporter(
        'DCGM_FI_DEV_XID_ERRORS{UUID="gpu-1"} 0\n', "cuda", collected_at=100
    )["devices"][0]
    assert device["health"]["severity"] == "unknown"
    assert device["metrics"]["health.error_code"]["timestamp_basis"] == "scrape"


def test_reject_ambiguous_or_missing_identity():
    with pytest.raises(ValueError, match="identity"):
        normalize_exporter('DCGM_FI_DEV_GPU_UTIL{gpu="0"} 0\n', "cuda")
    with pytest.raises(ValueError, match="duplicate"):
        normalize_exporter(
            'DCGM_FI_DEV_GPU_UTIL{UUID="x",pod="a"} 0\nDCGM_FI_DEV_GPU_UTIL{UUID="x",pod="b"} 0\n',
            "cuda",
        )
