# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Owned engine events; no SDK handle is reinterpreted across platforms."""

import math
import os

import torch


def tensor_platform(tensor):
    if tensor.device.type == "npu":
        platform = "ascend"
    elif tensor.device.type == "cuda":
        platform = "metax" if getattr(torch.version, "maca", None) else "cuda"
    else:
        raise ValueError(f"KVBM device is unsupported: {tensor.device}")
    selected = os.environ.get("DYN_PLATFORM", platform)
    if selected != platform:
        raise ValueError(f"KVBM tensor is {platform}, selected provider is {selected}")
    return platform


class PlatformEvent:
    """Keeps the SDK event alive until the Rust worker releases its last owner."""

    def __init__(self, tensor):
        self.platform = tensor_platform(tensor)
        self.device_index = tensor.device.index
        self._api = torch.npu if self.platform == "ascend" else torch.cuda
        with self._api.device(self.device_index):
            self._event = self._api.Event(enable_timing=False)
            self.record()

    def record(self):
        with self._api.device(self.device_index):
            self._event.record(self._api.current_stream(self.device_index))

    def synchronize(self):
        with self._api.device(self.device_index):
            self._event.synchronize()


def block_axis(tensor, num_blocks):
    """Accept dense NHD/HND tensors with an unambiguous engine block count."""
    candidates = [i for i, length in enumerate(tensor.shape) if length == num_blocks]
    if len(candidates) != 1:
        raise ValueError(
            f"Ambiguous KV block axis: shape={tuple(tensor.shape)}, blocks={num_blocks}"
        )
    if tensor.device.type == "npu":
        import torch_npu

        # Only ND exposes the dense storage described by Python shape/stride.
        # NZ and other blocked physical formats need their own layout provider.
        if torch_npu.get_npu_format(tensor) != 2:
            raise ValueError("KVBM requires Ascend ND storage format")
    if tensor.layout != torch.strided:
        raise ValueError("KVBM requires strided dense KV tensors")
    if not _dense_strides(tensor):
        raise ValueError("KVBM rejects overlapping or non-dense KV tensors")
    return candidates[0]


def _dense_strides(tensor):
    stride = 1
    for length, actual in sorted(
        ((n, s) for n, s in zip(tensor.shape, tensor.stride()) if n > 1),
        key=lambda item: item[1],
    ):
        if actual != stride:
            return False
        stride *= length
    return True


def coalesce_interleaved_kv(key, value):
    """Recover Ascend's dense [blocks, K/V, tokens, heads, dim] allocation.

    Ascend 0.30 exposes alternating K/V pages as two non-dense views.
    Only combine exact complementary views; never copy or widen a sparse view.
    Independent dense tensors retain their existing registration path.
    """
    if key.layout != torch.strided or value.layout != torch.strided:
        raise ValueError("KVBM requires strided dense KV tensors")
    if _dense_strides(key) and _dense_strides(value):
        return None
    if (
        key.shape != value.shape
        or key.dtype != value.dtype
        or key.device != value.device
        or key.ndim != 4
        or any(n <= 0 for n in key.shape)
    ):
        raise ValueError("Unsupported Ascend interleaved K/V descriptors")
    if key.device.type == "npu":
        import torch_npu

        if any(torch_npu.get_npu_format(t) != 2 for t in (key, value)):
            raise ValueError("KVBM requires Ascend ND storage format")
    page_elements = math.prod(key.shape[1:])
    expected = (
        2 * page_elements,
        *[math.prod(key.shape[i + 1 :]) for i in range(1, 4)],
    )
    key_storage, value_storage = key.untyped_storage(), value.untyped_storage()
    end = key.storage_offset() + 2 * key.shape[0] * page_elements
    if (
        key.stride() != expected
        or value.stride() != expected
        or key_storage.data_ptr() != value_storage.data_ptr()
        or key_storage.nbytes() != value_storage.nbytes()
        or value.storage_offset() != key.storage_offset() + page_elements
        or end * key.element_size() > key_storage.nbytes()
    ):
        raise ValueError("K/V views must exactly partition shared interleaved pages")
    return key.as_strided(
        (key.shape[0], 2, *key.shape[1:]),
        (2 * page_elements, page_elements, *key.stride()[1:]),
        key.storage_offset(),
    )
