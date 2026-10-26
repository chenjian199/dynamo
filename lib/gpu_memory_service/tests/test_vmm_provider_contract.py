# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Provider routing and ownership failures without a device SDK."""

import os
from types import SimpleNamespace

import pytest
from gpu_memory_service.common import vmm
from gpu_memory_service.common.locks import GrantedLockType
from gpu_memory_service.common.vmm.metax import MetaXVMM

pytestmark = [pytest.mark.pre_merge, pytest.mark.unit, pytest.mark.gpu_0]


@pytest.fixture(autouse=True)
def singleton():
    vmm._reset_vmm_singleton()
    yield
    vmm._reset_vmm_singleton()


def test_explicit_platform_and_immutable_selection(monkeypatch):
    monkeypatch.setenv("DYN_PLATFORM", "metax")
    fake = object()
    monkeypatch.setattr(vmm, "_create_vmm", lambda kind: fake)
    assert vmm.get_vmm() is fake
    assert vmm.get_vmm_device_type() == vmm.VMMDeviceType.METAX
    vmm.init_vmm(vmm.VMMDeviceType.METAX)
    with pytest.raises(RuntimeError, match="cannot reinitialize"):
        vmm.init_vmm(vmm.VMMDeviceType.CUDA)


def test_ascend_rejects_unverified_handle_and_permissions(monkeypatch):
    monkeypatch.setenv("DYN_PLATFORM", "ascend")
    with pytest.raises(NotImplementedError, match="read-only"):
        vmm.get_vmm()
    assert vmm._vmm_instance is None


def test_maca_alias_precedes_cuda(monkeypatch):
    import sys

    monkeypatch.delenv("DYN_PLATFORM", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            version=SimpleNamespace(maca="3.8.2"),
            cuda=SimpleNamespace(is_available=lambda: pytest.fail("CUDA probe")),
        ),
    )
    assert vmm._detect_device_type() == vmm.VMMDeviceType.METAX


def test_failed_import_still_closes_fd(monkeypatch):
    provider = MetaXVMM.__new__(MetaXVMM)

    def fail(*args):
        raise RuntimeError("import failed")

    monkeypatch.setattr(provider, "_call", fail)
    read, write = os.pipe()
    os.close(write)
    with pytest.raises(RuntimeError, match="import failed"):
        provider.import_shareable_handle_close_fd(read)
    with pytest.raises(OSError):
        os.fstat(read)


def test_permission_success_code_is_not_enough(monkeypatch):
    provider = MetaXVMM.__new__(MetaXVMM)
    monkeypatch.setattr(provider, "runtime_set_device", lambda device: None)

    def call(name, *args):
        if name == "mcMemGetAccess":
            args[0]._obj.value = 3

    monkeypatch.setattr(provider, "_call", call)
    with pytest.raises(RuntimeError, match="did not apply"):
        provider.set_access(4096, 4096, 0, GrantedLockType.RO)


def test_forked_provider_fails_before_sdk_call():
    provider = MetaXVMM.__new__(MetaXVMM)
    provider._pid = -1
    with pytest.raises(RuntimeError, match="after spawn"):
        provider._call("mcDeviceSynchronize")
