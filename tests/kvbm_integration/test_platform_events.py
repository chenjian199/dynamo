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


@pytest.fixture
def real_events():
    path = (
        Path(__file__).parents[2] / "lib/bindings/kvbm/python/kvbm/platform_events.py"
    )
    spec = importlib.util.spec_from_file_location("kvbm_real_layout_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_interleaved_pair_retains_storage_and_both_directions(real_events):
    import torch

    # Include a nonzero storage offset to test the retained allocation boundary.
    backing = torch.arange(3 + 7 * 2 * 16 * 4 * 8, dtype=torch.float32)
    dense = backing[3:].view(7, 2, 16, 4, 8)
    key, value = dense[:, 0], dense[:, 1]
    combined = real_events.coalesce_interleaved_kv(key, value)
    assert combined.data_ptr() == key.data_ptr()
    assert real_events.block_axis(combined, 7) == 0
    assert torch.equal(combined, dense)
    combined[2, 1].fill_(-7)
    assert torch.all(value[2] == -7)
    key[3].fill_(19)
    assert torch.all(combined[3, 0] == 19)


def test_dense_independent_permutations_keep_existing_path(real_events):
    import torch

    key = torch.empty(7, 16, 4, 8).transpose(1, 2)
    value = torch.empty_like(key)
    assert real_events.coalesce_interleaved_kv(key, value) is None


@pytest.mark.parametrize("invalid", ["overlap", "different_storage", "wrong_stride"])
def test_interleaved_pair_rejects_unproven_storage(real_events, invalid):
    import torch

    dense = torch.empty(7, 2, 16, 4, 8)
    key, value = dense[:, 0], dense[:, 1]
    if invalid == "overlap":
        value = key
    elif invalid == "different_storage":
        value = torch.empty_like(dense)[:, 1]
    else:
        value = value.transpose(1, 2)
    with pytest.raises(ValueError):
        real_events.coalesce_interleaved_kv(key, value)
