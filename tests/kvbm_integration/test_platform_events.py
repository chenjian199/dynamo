# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Device event and layout boundaries without loading a vendor SDK."""

import contextlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = [pytest.mark.pre_merge, pytest.mark.gpu_0, pytest.mark.unit]


@pytest.fixture
def events(monkeypatch):
    api = Mock()
    api.device.side_effect = lambda _: contextlib.nullcontext()
    torch = SimpleNamespace(
        cuda=api, npu=api, version=SimpleNamespace(), strided="strided"
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.delenv("DYN_PLATFORM", raising=False)
    path = (
        Path(__file__).parents[2] / "lib/bindings/kvbm/python/kvbm/platform_events.py"
    )
    spec = importlib.util.spec_from_file_location("kvbm_platform_events_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, torch, api


@pytest.mark.parametrize(
    "platform,kind", [("cuda", "cuda"), ("metax", "cuda"), ("ascend", "npu")]
)
def test_owned_event_uses_selected_device(events, platform, kind):
    module, torch, api = events
    if platform == "metax":
        torch.version.maca = "3.8.2"
    tensor = SimpleNamespace(device=SimpleNamespace(type=kind, index=1))
    event = module.PlatformEvent(tensor)
    assert (event.platform, event.device_index) == (platform, 1)
    event.record()
    event.synchronize()
    api.device.assert_called_with(1)
    api.current_stream.assert_called_with(1)
    api.Event.return_value.synchronize.assert_called_once_with()


def test_provider_mismatch_fails_before_sdk_event(events, monkeypatch):
    module, _, api = events
    monkeypatch.setenv("DYN_PLATFORM", "metax")
    with pytest.raises(ValueError, match="selected provider"):
        module.PlatformEvent(
            SimpleNamespace(device=SimpleNamespace(type="cuda", index=0))
        )
    api.Event.assert_not_called()


def test_layout_accepts_dense_permutations_and_rejects_aliases(events):
    module, _, _ = events
    tensor = SimpleNamespace(
        shape=(2, 7, 16, 4, 32), layout="strided", device=SimpleNamespace(type="cuda")
    )
    tensor.stride = lambda: (14336, 2048, 32, 512, 1)
    assert module.block_axis(tensor, 7) == 1
    tensor.stride = lambda: (14336, 2048, 32, 0, 1)
    with pytest.raises(ValueError, match="overlapping"):
        module.block_axis(tensor, 7)
    tensor.shape = (2, 2, 16, 4, 32)
    with pytest.raises(ValueError, match="Ambiguous"):
        module.block_axis(tensor, 2)
