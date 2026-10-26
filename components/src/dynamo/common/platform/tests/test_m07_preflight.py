# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dynamo.common.constants import DisaggregationMode
from dynamo.common.platform import preflight

pytestmark = [pytest.mark.pre_merge, pytest.mark.unit, pytest.mark.gpu_0]


@pytest.mark.parametrize("v1", [False, True])
def test_ascend_gms_rejected_before_engine_initialization(monkeypatch, v1):
    monkeypatch.setenv("DYN_PLATFORM", "ascend")
    monkeypatch.setenv("DYN_GMS_USE_V1", str(v1).lower())
    config = SimpleNamespace(
        engine_args=SimpleNamespace(load_format="auto" if v1 else "gms")
    )
    with pytest.raises(preflight.PlatformError, match="rejects read-only access"):
        preflight.prepare_vllm_platform(config)


def test_encoder_does_not_require_pd_transport(monkeypatch):
    monkeypatch.setenv("DYN_PLATFORM", "metax")
    monkeypatch.delenv("DYN_GMS_USE_V1", raising=False)
    monkeypatch.setattr(preflight, "configure_platform_scheduler", Mock())
    registry = Mock()
    registry.prepare.return_value = {"devices": [{}]}
    monkeypatch.setattr(preflight, "registry", registry)
    config = SimpleNamespace(
        engine_args=SimpleNamespace(
            tensor_parallel_size=1,
            data_parallel_size=1,
            data_parallel_size_local=1,
            pipeline_parallel_size=1,
            enforce_eager=True,
            kv_transfer_config=None,
            enable_lora=False,
            quantization=None,
            speculative_config=None,
            enable_expert_parallel=False,
        ),
        headless=False,
        embedding_worker=False,
        enable_multimodal=True,
        disaggregation_mode=DisaggregationMode.ENCODE,
        use_kv_events=False,
        model="vision",
    )
    preflight.prepare_vllm_platform(config)
    assert "pd.transfer" not in registry.prepare.call_args.args[2]
    assert registry.prepare.call_args.args[1]["pd_connector"] == "none"
