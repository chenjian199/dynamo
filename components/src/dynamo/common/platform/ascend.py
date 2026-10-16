# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ascend discovery provider for the vLLM Ascend 0.23.0 / CANN 9.1 image.

The 910B torch-npu UUID is all zero. DCMI's silicon die ID supplies a stable
opaque identity; ACL maps visible ordinals to physical IDs before DCMI queries.
FFI layouts follow the installed 25.2 driver dcmi_interface_api.h.
"""

import ctypes
import importlib
import importlib.metadata
from pathlib import Path

from dynamo.common.platform.providers import build_capabilities
from dynamo.common.platform.registry import PlatformError


class _DieId(ctypes.Structure):
    _fields_ = [("soc_die", ctypes.c_uint * 5)]


class _PcieInfo(ctypes.Structure):
    _fields_ = [
        ("vendor_id", ctypes.c_uint),
        ("subvendor_id", ctypes.c_uint),
        ("device_id", ctypes.c_uint),
        ("subdevice_id", ctypes.c_uint),
        ("domain", ctypes.c_int),
        ("bus", ctypes.c_uint),
        ("device", ctypes.c_uint),
        ("function", ctypes.c_uint),
        ("reserved", ctypes.c_ubyte * 32),
    ]


def _check(result, operation):
    if result != 0:
        raise PlatformError(
            "device_probe_failed", "ascend", f"{operation}: SDK error {result}"
        )


def _version(path, key):
    for line in Path(path).read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == key and value.strip():
            return value.strip()
    raise PlatformError("missing_stack_version", "ascend", f"{key} absent in {path}")


class _Dcmi:
    def __init__(self):
        self.library = ctypes.CDLL("/usr/local/dcmi/libdcmi.so")
        signatures = {
            "dcmi_init": [],
            "dcmi_get_card_id_device_id_from_phyid": [
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_uint,
            ],
            "dcmi_get_device_die": [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_DieId)],
            "dcmi_get_device_pcie_info_v2": [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_PcieInfo),
            ],
        }
        for name, parameters in signatures.items():
            function = getattr(self.library, name)
            function.argtypes = parameters
            function.restype = ctypes.c_int
        _check(self.library.dcmi_init(), "dcmi_init")

    def identity(self, physical_id):
        card, device = ctypes.c_int(), ctypes.c_int()
        _check(
            self.library.dcmi_get_card_id_device_id_from_phyid(
                ctypes.byref(card), ctypes.byref(device), physical_id
            ),
            "dcmi_get_card_id_device_id_from_phyid",
        )
        die, pci = _DieId(), _PcieInfo()
        _check(
            self.library.dcmi_get_device_die(
                card.value, device.value, ctypes.byref(die)
            ),
            "dcmi_get_device_die",
        )
        if not any(die.soc_die):
            raise PlatformError(
                "missing_device_identity",
                "ascend",
                "driver returned an empty silicon die ID",
            )
        _check(
            self.library.dcmi_get_device_pcie_info_v2(
                card.value, device.value, ctypes.byref(pci)
            ),
            "dcmi_get_device_pcie_info_v2",
        )
        return (
            "ascend-die:" + "".join(f"{word:08x}" for word in die.soc_die),
            f"{pci.domain:04x}:{pci.bus:02x}:{pci.device:02x}.{pci.function:x}",
        )


def ascend(context):
    torch = importlib.import_module("torch")
    importlib.import_module("torch_npu")
    acl = importlib.import_module("acl")
    if not torch.npu.is_available():
        raise PlatformError("missing_device", "ascend", "no visible NPU")
    software = {
        name: importlib.metadata.version(name)
        for name in ("torch", "torch-npu", "vllm-ascend")
    }
    software["driver"] = _version("/usr/local/Ascend/driver/version.info", "Version")
    software["cann"] = _version(
        "/usr/local/Ascend/ascend-toolkit/latest/arm64-linux/ascend_toolkit_install.info",
        "version",
    )
    dcmi = _Dcmi()
    devices = []
    for ordinal in range(torch.npu.device_count()):
        physical_id, result = acl.rt.get_phy_dev_id_by_logic_dev_id(ordinal)
        _check(result, "acl.rt.get_phy_dev_id_by_logic_dev_id")
        uuid, pci = dcmi.identity(physical_id)
        properties = torch.npu.get_device_properties(ordinal)
        devices.append(
            dict(
                ordinal=ordinal,
                uuid=uuid,
                pci_bus_id=pci,
                architecture=properties.name,
                memory_bytes=properties.total_memory,
            )
        )
    return dict(
        schema_version=1,
        platform_id="ascend",
        device_kind="accelerator",
        provider_version="1",
        software=software,
        devices=devices,
        capabilities=build_capabilities("ascend", context, software),
    )
