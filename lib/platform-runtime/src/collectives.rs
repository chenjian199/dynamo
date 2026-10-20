// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Owned NCCL/MCCL/HCCL communicators. ABI declarations follow installed vendor headers.

use super::{NativeBackend, Ptr, Vendor, load, symbol};
use dynamo_platform::device::{DeviceBackend, DeviceResult, MemorySpace};
use dynamo_platform::memory::Allocation;
use libloading::Library;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

#[repr(C)]
#[derive(Clone, Copy)]
struct UniqueId {
    bytes: [u8; 128],
}

pub struct NativeCommunicator {
    library: Arc<Library>,
    backend: Arc<NativeBackend>,
    comm: usize,
    stream: usize,
    rank: u32,
    world_size: u32,
    poisoned: bool,
}

fn library(backend: &NativeBackend) -> DeviceResult<Arc<Library>> {
    // GetUniqueId can start bootstrap threads before a communicator exists.
    // Vendor communication libraries therefore remain loaded for the process.
    static CUDA: OnceLock<DeviceResult<Arc<Library>>> = OnceLock::new();
    static METAX: OnceLock<DeviceResult<Arc<Library>>> = OnceLock::new();
    static ASCEND: OnceLock<DeviceResult<Arc<Library>>> = OnceLock::new();
    let (slot, name) = match backend.vendor {
        Vendor::Cuda => (&CUDA, "libnccl.so.2"),
        Vendor::Metax => (&METAX, "libmccl.so"),
        Vendor::Ascend => (&ASCEND, "libhccl.so"),
    };
    slot.get_or_init(|| load(name, &backend.identity.platform).map(Arc::new))
        .clone()
}

impl NativeCommunicator {
    pub fn root_info(backend: &NativeBackend) -> DeviceResult<Vec<u8>> {
        let lib = library(backend)?;
        let mut bytes = vec![
            0;
            if backend.vendor == Vendor::Ascend {
                4108
            } else {
                128
            }
        ];
        let name = match backend.vendor {
            Vendor::Cuda => "ncclGetUniqueId",
            Vendor::Metax => "mcclGetUniqueId",
            Vendor::Ascend => "HcclGetRootInfo",
        };
        let get: unsafe extern "C" fn(*mut u8) -> i32 =
            unsafe { symbol(&lib, name, &backend.identity.platform)? };
        backend
            .bound(|| backend.check("collective_root_info", unsafe { get(bytes.as_mut_ptr()) }))?;
        Ok(bytes)
    }

    /// Collective initialization: every rank must call with identical root info.
    /// Run in separate processes with a supervisor deadline for bootstrap failures.
    pub fn new(
        backend: Arc<NativeBackend>,
        root: &[u8],
        rank: u32,
        world_size: u32,
    ) -> DeviceResult<Self> {
        if rank >= world_size || world_size == 0 || world_size > i32::MAX as u32 {
            return Err(backend.error("invalid_collective", "invalid rank/world size"));
        }
        let lib = library(&backend)?;
        let mut comm = std::ptr::null_mut();
        backend.bound(|| unsafe {
            let rc = if backend.vendor == Vendor::Ascend {
                if root.len() != 4108 {
                    return Err(backend.error(
                        "invalid_collective",
                        "HCCL root info must contain 4108 bytes",
                    ));
                }
                let init: unsafe extern "C" fn(u32, *const u8, u32, *mut Ptr) -> i32 =
                    symbol(&lib, "HcclCommInitRootInfo", &backend.identity.platform)?;
                init(world_size, root.as_ptr(), rank, &mut comm)
            } else {
                let bytes: [u8; 128] = root.try_into().map_err(|_| {
                    backend.error("invalid_collective", "NCCL/MCCL ID must contain 128 bytes")
                })?;
                let name = if backend.vendor == Vendor::Cuda {
                    "ncclCommInitRank"
                } else {
                    "mcclCommInitRank"
                };
                let init: unsafe extern "C" fn(*mut Ptr, i32, UniqueId, i32) -> i32 =
                    symbol(&lib, name, &backend.identity.platform)?;
                init(
                    &mut comm,
                    world_size as i32,
                    UniqueId { bytes },
                    rank as i32,
                )
            };
            backend.check("collective_init", rc)
        })?;
        if comm.is_null() {
            return Err(backend.error("invalid_collective", "null communicator"));
        }
        let mut result = Self {
            library: lib,
            backend,
            comm: comm as usize,
            stream: 0,
            rank,
            world_size,
            poisoned: false,
        };
        result.stream = unsafe { result.backend.create_stream()? };
        Ok(result)
    }

    pub fn rank(&self) -> u32 {
        self.rank
    }
    pub fn world_size(&self) -> u32 {
        self.world_size
    }
    pub fn identity(&self) -> &dynamo_platform::device::DeviceIdentity {
        self.backend.identity()
    }

    /// Blocking, event-verified in-place byte broadcast from rank zero.
    /// # Safety
    /// Every rank must use the same collective order and byte count. The buffer
    /// is exclusively leased until this method completes; it must be initialized.
    pub unsafe fn broadcast(
        &mut self,
        buffer: &Allocation,
        bytes: usize,
        timeout: Duration,
    ) -> DeviceResult<()> {
        if self.poisoned
            || buffer.identity() != self.backend.identity()
            || buffer.space() != MemorySpace::Device
            || bytes == 0
            || bytes > buffer.len()
        {
            return Err(self.backend.error(
                "invalid_collective",
                "poisoned communicator or invalid buffer",
            ));
        }
        let event = unsafe { self.backend.create_event()? };
        let result = self
            .backend
            .bound(|| unsafe {
                let rc = match self.backend.vendor {
                    Vendor::Ascend => {
                        let f: unsafe extern "C" fn(Ptr, u64, i32, u32, Ptr, Ptr) -> i32 = symbol(
                            &self.library,
                            "HcclBroadcast",
                            &self.backend.identity.platform,
                        )?;
                        f(
                            buffer.address() as Ptr,
                            bytes as u64,
                            7,
                            0,
                            self.comm as Ptr,
                            self.stream as Ptr,
                        )
                    }
                    vendor => {
                        let name = if vendor == Vendor::Cuda {
                            "ncclBcast"
                        } else {
                            "mcclBcast"
                        };
                        let f: unsafe extern "C" fn(Ptr, usize, i32, i32, Ptr, Ptr) -> i32 =
                            symbol(&self.library, name, &self.backend.identity.platform)?;
                        f(
                            buffer.address() as Ptr,
                            bytes,
                            1,
                            0,
                            self.comm as Ptr,
                            self.stream as Ptr,
                        )
                    }
                };
                self.backend.check("collective_broadcast", rc)
            })
            .and_then(|()| unsafe { self.backend.record_event(event, self.stream) });
        let deadline = Instant::now() + timeout;
        let result = result.and_then(|()| {
            loop {
                match unsafe { self.backend.query_event(event) } {
                    Ok(true) => break Ok(()),
                    Ok(false) if Instant::now() < deadline => {
                        std::thread::sleep(Duration::from_millis(1))
                    }
                    Ok(false) => {
                        break Err(self
                            .backend
                            .error("collective_timeout", "broadcast did not complete"));
                    }
                    Err(e) => break Err(e),
                }
            }
        });
        if result.is_err() {
            self.poisoned = true;
            // No portable HCCL abort contract exists in the installed SDK. Do
            // not release buffers or report completion while DMA may be live.
            if !matches!(unsafe { self.backend.query_stream(self.stream) }, Ok(true)) {
                tracing::error!(
                    "collective failed with pending device work; terminating to preserve leases"
                );
                std::process::abort();
            }
        }
        unsafe { self.backend.destroy_event(event)? };
        result
    }
}

impl Drop for NativeCommunicator {
    fn drop(&mut self) {
        let result = self.backend.bound(|| unsafe {
            let name = match self.backend.vendor {
                Vendor::Cuda => "ncclCommDestroy",
                Vendor::Metax => "mcclCommDestroy",
                Vendor::Ascend => "HcclCommDestroy",
            };
            let destroy: unsafe extern "C" fn(Ptr) -> i32 =
                symbol(&self.library, name, &self.backend.identity.platform)?;
            self.backend
                .check("collective_destroy", destroy(self.comm as Ptr))
        });
        if let Err(error) = result {
            tracing::error!(%error, "collective destruction failed");
        }
        if self.stream != 0
            && let Err(error) = unsafe { self.backend.destroy_stream(self.stream) }
        {
            tracing::error!(%error, "collective stream destruction failed");
        }
    }
}
