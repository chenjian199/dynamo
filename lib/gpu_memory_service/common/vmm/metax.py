# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MACA VMM using its POSIX sharing ABI; no CUDA imports or handle casts."""

import ctypes as C
import os
import threading
from uuid import UUID

from gpu_memory_service.common.locks import GrantedLockType
from gpu_memory_service.common.vmm.device import VMMDevice


class Location(C.Structure):
    _fields_ = [("type", C.c_int), ("id", C.c_int)]


class Properties(C.Structure):
    _fields_ = [
        ("type", C.c_int),
        ("handle_type", C.c_int),
        ("location", Location),
        ("windows_metadata", C.c_void_p),
        ("flags", C.c_ubyte * 8),
    ]


class Access(C.Structure):
    _fields_ = [("location", Location), ("flags", C.c_int)]


class MetaXVMM(VMMDevice):
    """Each GMS sidecar owns one device. Callers bind it on each SDK thread."""

    access_requires_remap = True

    def __init__(self):
        self.lib = C.CDLL("libmcruntime.so")
        self._pid = os.getpid()
        self._device = None
        self._device_lock = threading.Lock()
        p, n, u, i = C.c_void_p, C.c_size_t, C.c_ulonglong, C.c_int
        signatures = {
            "mcGetDeviceCount": [C.POINTER(i)],
            "mcSetDevice": [i],
            "mcDeviceSynchronize": [],
            "mcMemGetInfo": [C.POINTER(n), C.POINTER(n)],
            "mcDeviceGetUuid": [p, i],
            "mcMemGetAllocationGranularity": [C.POINTER(n), C.POINTER(Properties), i],
            "mcMemCreate": [C.POINTER(u), n, C.POINTER(Properties), u],
            "mcMemRelease": [u],
            "mcMemExportToShareableHandle": [p, u, i, u],
            "mcMemImportFromShareableHandle": [C.POINTER(u), p, i],
            "mcMemAddressReserve": [C.POINTER(u), n, n, u, u],
            "mcMemAddressFree": [u, n],
            "mcMemMap": [u, n, n, u, u],
            "mcMemUnmap": [u, n],
            "mcMemSetAccess": [u, n, C.POINTER(Access), n],
            "mcMemGetAccess": [C.POINTER(u), C.POINTER(Location), u],
            "mcMemRetainAllocationHandle": [C.POINTER(u), p],
            "mcHostRegister": [p, n, C.c_uint],
            "mcHostUnregister": [p],
            "mcStreamCreateWithFlags": [C.POINTER(p), C.c_uint],
            "mcStreamDestroy": [p],
            "mcStreamSynchronize": [p],
            "mcMemcpyAsync": [p, p, n, i, p],
        }
        for name, args in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, i
        self.lib.mcGetErrorString.argtypes = [i]
        self.lib.mcGetErrorString.restype = C.c_char_p

    def runtime_check_result(self, result, name):
        if result:
            message = self.lib.mcGetErrorString(result)
            raise RuntimeError(f"MACA {name} failed ({result}): {message!r}")

    def _call(self, name, *args):
        if os.getpid() != self._pid:
            raise RuntimeError(
                "MACA VMM must be initialized after spawn, not inherited by fork"
            )
        if self._device is not None and name != "mcSetDevice":
            self.runtime_check_result(self.lib.mcSetDevice(self._device), "mcSetDevice")
        self.runtime_check_result(getattr(self.lib, name)(*args), name)

    def ensure_initialized(self):
        count = C.c_int()
        self._call("mcGetDeviceCount", C.byref(count))
        if count.value == 0:
            raise RuntimeError("no visible MACA devices")

    def runtime_set_device(self, device):
        if os.getpid() != self._pid:
            raise RuntimeError("MACA VMM must be initialized after spawn")
        with self._device_lock:
            if self._device is not None and device != self._device:
                raise RuntimeError("one MACA VMM instance must remain on one device")
            self._call("mcSetDevice", device)
            self._device = device

    def list_devices(self):
        count = C.c_int()
        self._call("mcGetDeviceCount", C.byref(count))
        return list(range(count.value))

    def device_uuid(self, device):
        value = (C.c_ubyte * 16)()
        self._call("mcDeviceGetUuid", value, device)
        return f"METAX-{UUID(bytes=bytes(value))}"

    def device_memory_info(self, device):
        self.runtime_set_device(device)
        free, total = C.c_size_t(), C.c_size_t()
        self._call("mcMemGetInfo", C.byref(free), C.byref(total))
        return free.value, total.value

    def _properties(self, device):
        self.runtime_set_device(device)
        prop = Properties()
        prop.type, prop.handle_type, prop.location = 1, 1, Location(1, device)
        return prop

    def get_allocation_granularity(self, device):
        prop = self._properties(device)
        value = C.c_size_t()
        self._call("mcMemGetAllocationGranularity", C.byref(value), C.byref(prop), 0)
        return value.value

    def create_tolerate_oom(self, size, device):
        prop = self._properties(device)
        handle = C.c_ulonglong()
        code = self.lib.mcMemCreate(C.byref(handle), size, C.byref(prop), 0)
        if code == 2:  # mcErrorMemoryAllocation, from installed MACA headers
            return False, 0
        self.runtime_check_result(code, "mcMemCreate")
        return True, handle.value

    def release(self, handle):
        self._call("mcMemRelease", handle)

    def export_to_shareable_handle(self, handle):
        fd = C.c_int(-1)
        self._call("mcMemExportToShareableHandle", C.byref(fd), handle, 1, 0)
        try:
            os.fstat(fd.value)
            os.set_inheritable(fd.value, False)
            return fd.value
        except BaseException:
            if fd.value >= 0:
                os.close(fd.value)
            raise

    def import_shareable_handle_close_fd(self, fd):
        try:
            handle = C.c_ulonglong()
            self._call(
                "mcMemImportFromShareableHandle", C.byref(handle), C.c_void_p(fd), 1
            )
            return handle.value
        finally:
            os.close(fd)

    def address_reserve(self, size, granularity):
        va = C.c_ulonglong()
        self._call("mcMemAddressReserve", C.byref(va), size, granularity, 0, 0)
        return va.value

    def address_free(self, va, size):
        self._call("mcMemAddressFree", va, size)

    def map(self, va, size, handle):
        self._call("mcMemMap", va, size, 0, handle, 0)

    def unmap(self, va, size):
        self._call("mcMemUnmap", va, size)

    def set_access(self, va, size, device, access):
        self.runtime_set_device(device)
        if access not in (GrantedLockType.RO, GrantedLockType.RW):
            raise ValueError("unsupported GMS mapping access")
        desc = Access(Location(1, device), 1 if access == GrantedLockType.RO else 3)
        self._call("mcMemSetAccess", va, size, C.byref(desc), 1)
        actual = C.c_ulonglong()
        self._call("mcMemGetAccess", C.byref(actual), C.byref(desc.location), va)
        if actual.value != desc.flags:
            raise RuntimeError(
                "MACA did not apply access permissions; unmap before changing access"
            )

    def validate_pointer(self, va):
        handle = C.c_ulonglong()
        self._call("mcMemRetainAllocationHandle", C.byref(handle), C.c_void_p(va))
        self.release(handle.value)

    def synchronize(self):
        self._call("mcDeviceSynchronize")

    def host_register(self, ptr, size):
        self._call("mcHostRegister", ptr, size, 0)

    def host_unregister(self, ptr):
        self._call("mcHostUnregister", ptr)

    def stream_create_nonblocking(self):
        stream = C.c_void_p()
        self._call("mcStreamCreateWithFlags", C.byref(stream), 1)
        return stream.value

    def stream_destroy(self, stream):
        self._call("mcStreamDestroy", stream)

    def stream_synchronize(self, stream):
        self._call("mcStreamSynchronize", stream)

    def memcpy_h2d_async(self, dst_ptr, src_ptr, size, stream):
        self._call("mcMemcpyAsync", dst_ptr, src_ptr, size, 1, stream)

    def memcpy_d2h_async(self, dst_ptr, src_ptr, size, stream):
        self._call("mcMemcpyAsync", dst_ptr, src_ptr, size, 2, stream)
