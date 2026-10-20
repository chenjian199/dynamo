# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Owned engine events; no SDK handle is reinterpreted across platforms."""

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
    stride = 1
    for length, actual in sorted(
        ((n, s) for n, s in zip(tensor.shape, tensor.stride()) if n > 1),
        key=lambda item: item[1],
    ):
        if actual != stride:
            raise ValueError("KVBM rejects overlapping or non-dense KV tensors")
        stride *= length
    return candidates[0]
