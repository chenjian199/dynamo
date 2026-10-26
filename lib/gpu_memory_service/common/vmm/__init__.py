# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Derived from NVIDIA Dynamo. Modified by HEYI.

"""GPU Memory Service — VMM device abstraction.

GMS depends on a per-vendor virtual-memory-management surface
(allocate physical memory, export/import shareable handles, reserve and
map virtual addresses, ...).

The VMM instance is process-global singleton and immutable once initialized.
- `init_vmm(device_type)`: explicit process-level backend selection, e.g.
   CLI `--device-type`. On a mixed node (e.g., CUDA and other device coexist),
   --device-type device_name overrides the auto-detection because it calls
   init_vmm(VMMDeviceType.DeviceName) at process startup — before any get_vmm()
   lazy path fires.
- `get_vmm()`: returns the singleton and may lazily initialize the default/autodetected
   backend for public client/integration compatibility via init_vmm(detected_device_type).
- `vmm.ensure_initialized()`: initializes the selected backend driver/runtime,
    e.g. CUDA `cuInit`

"""

from __future__ import annotations

import importlib.util
import os
import threading
from enum import Enum

from .device import VMMDevice


class VMMDeviceType(str, Enum):
    """Identify which vendor's VMM driver a GMS instance should use."""

    CUDA = "cuda"
    XPU = "xpu"
    METAX = "metax"
    ASCEND = "ascend"

    @classmethod
    def from_str(cls, value: str) -> "VMMDeviceType":
        try:
            return cls(value.lower())
        except ValueError as exc:
            valid = ", ".join(b.value for b in cls)
            raise ValueError(
                f"Unknown VMM device type {value!r}; expected one of: {valid}"
            ) from exc


# ---------------------------------------------------------------------------
# Process-global singleton
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_vmm_instance: VMMDevice | None = None
_vmm_device_type: VMMDeviceType | None = None


def init_vmm(device_type: VMMDeviceType) -> None:
    """Initialize the process-global VMM singleton. Idempotent for same kind."""
    global _vmm_instance, _vmm_device_type
    with _lock:
        if _vmm_instance is not None:
            if _vmm_device_type != device_type:
                raise RuntimeError(
                    f"VMM already initialized as {_vmm_device_type!r}; "
                    f"cannot reinitialize as {device_type!r}"
                )
            return
        inst = _create_vmm(device_type)
        _vmm_device_type = device_type
        _vmm_instance = inst


def _detect_device_type() -> VMMDeviceType:
    """Auto-detect the available accelerator device type at runtime.

    Explicit DYN_PLATFORM wins; vendor runtimes precede CUDA-compatible APIs.
    """
    explicit = os.environ.get("DYN_PLATFORM")
    if explicit:
        return VMMDeviceType.from_str(explicit)
    try:
        import torch

        if getattr(torch.version, "maca", None):
            return VMMDeviceType.METAX
        if hasattr(torch, "npu") or importlib.util.find_spec("torch_npu") is not None:
            return VMMDeviceType.ASCEND

        if torch.cuda.is_available():
            return VMMDeviceType.CUDA
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            return VMMDeviceType.XPU
    except Exception:
        pass
    return VMMDeviceType.CUDA


def get_vmm() -> VMMDevice:
    """Return the process-global VMM singleton.

    If ``init_vmm()`` has not been called, lazily auto-detects the device
    type via ``_detect_device_type()`` and initializes. This preserves
    backward compatibility for integration paths (vLLM, SGLang, TRTLLM,
    gms-storage-client) that construct helpers without explicitly
    bootstrapping the VMM singleton.

    Explicit ``init_vmm(device_type)`` calls (e.g. from CLI ``--device-type``)
    still take priority since they run before any ``get_vmm()`` call.
    """
    inst = _vmm_instance
    if inst is None:
        init_vmm(_detect_device_type())
        inst = _vmm_instance
    return inst  # type: ignore[return-value]


def get_vmm_device_type() -> VMMDeviceType:
    """Return the active device type.

    Lazily auto-detects if ``init_vmm()`` has not been called.
    """
    kind = _vmm_device_type
    if kind is None:
        init_vmm(_detect_device_type())
        kind = _vmm_device_type
    return kind  # type: ignore[return-value]


def _create_vmm(device_type: VMMDeviceType) -> VMMDevice:
    """Construct the appropriate VMMDevice implementation."""
    if device_type is VMMDeviceType.CUDA:
        from .cuda_utils import CudaVMM

        return CudaVMM()

    if device_type is VMMDeviceType.METAX:
        from .metax import MetaXVMM

        return MetaXVMM()

    if device_type is VMMDeviceType.ASCEND:
        raise NotImplementedError(
            "Ascend GMS requires verified read-only VMM access and a versioned "
            "sharing protocol; 910B3/CANN 9.1/HDK 26.1.1 rejects READ access "
            "(207000) and POSIX allocation (100000); no Ascend GMS provider"
        )

    if device_type is VMMDeviceType.XPU:
        raise NotImplementedError("'xpu' VMM backend is not implemented yet")

    raise ValueError(f"Unhandled VMM device type: {device_type!r}")


# ---------------------------------------------------------------------------
# Test support
# ---------------------------------------------------------------------------


def _reset_vmm_singleton() -> None:
    """Reset the singleton for test isolation. NOT for production use."""
    global _vmm_instance, _vmm_device_type
    with _lock:
        _vmm_instance = None
        _vmm_device_type = None


__all__ = [
    "VMMDevice",
    "VMMDeviceType",
    "get_vmm",
    "get_vmm_device_type",
    "init_vmm",
]
