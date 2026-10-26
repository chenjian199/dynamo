# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Host-staged media contracts shared by embedding and decoded-media consumers."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import math
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import torch

MAX_TENSOR_BYTES = 8 * 1024**3
_DTYPES = frozenset(
    (
        "float16",
        "bfloat16",
        "float32",
        "float64",
        "int8",
        "uint8",
        "int16",
        "int32",
        "int64",
        "bool",
    )
)


def selected_platform() -> str:
    """Resolve vendor identity before interpreting a CUDA-compatible torch API."""
    explicit = os.environ.get("DYN_PLATFORM")
    if explicit:
        if explicit not in ("cpu", "cuda", "metax", "ascend", "xpu"):
            raise ValueError(f"unknown media platform: {explicit}")
        return explicit
    torch = sys.modules.get("torch")
    if torch is not None and getattr(torch.version, "maca", None):
        return "metax"
    if torch is not None and hasattr(torch, "npu"):
        return "ascend"
    try:
        if "metax" in importlib.metadata.version("torch").lower():
            return "metax"
    except importlib.metadata.PackageNotFoundError:
        pass
    if importlib.util.find_spec("torch_npu") is not None:
        return "ascend"
    # Preserve legacy CUDA invocation without probing or initializing a device.
    return "cuda"


def tensor_dtype(name: str) -> torch.dtype:
    if not isinstance(name, str) or name.removeprefix("torch.") not in _DTYPES:
        raise ValueError(f"unsupported media tensor dtype: {name!r}")
    import torch

    return getattr(torch, name.removeprefix("torch."))


def tensor_nbytes(shape, dtype: str) -> int:
    if not isinstance(shape, (list, tuple)) or not 1 <= len(shape) <= 8:
        raise ValueError("media tensor must have 1 to 8 dimensions")
    if any(type(dim) is not int or dim <= 0 for dim in shape):
        raise ValueError("media tensor dimensions must be positive integers")
    size = math.prod(shape) * tensor_dtype(dtype).itemsize
    if size > MAX_TENSOR_BYTES:
        raise ValueError("media tensor exceeds the 8 GiB transfer limit")
    return size


def transfer_tensor(tensor: torch.Tensor) -> torch.Tensor:
    """Use CPU transport for vendor devices until a typed descriptor is available."""
    tensor_nbytes(list(tensor.shape), str(tensor.dtype))
    tensor = tensor.detach().contiguous()
    if tensor.device.type != "cpu" and selected_platform() != "cuda":
        tensor = tensor.cpu()
    return tensor
