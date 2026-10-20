// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Selected SDK loading; no CUDA/MACA/CANN dependency is linked or loaded at import.
//! API declarations are checked against CUDA runtime/driver, MACA 3.8.2 headers,
//! and CANN 9.1 acl_rt.h. Never treat a CUDA-compatible Python API as a native ABI.

use dynamo_platform::PlatformError;
use dynamo_platform::device::{DeviceBackend, DeviceIdentity, DeviceResult, MemorySpace};
use libloading::Library;
use std::ffi::c_void;
use std::sync::Arc;

pub mod kv;
pub mod collectives;

type Ptr = *mut c_void;
type GetContext = unsafe extern "C" fn(*mut Ptr) -> i32;
type SetContext = unsafe extern "C" fn(Ptr) -> i32;

// Restoration must run on errors and panic unwinding. A restoration failure
// cannot erase a successful allocation/registration result: doing so loses its
// ownership. Report it separately and let the next operation explicitly rebind.
struct ContextGuard {
    set: SetContext,
    previous: usize,
    restore: bool,
}
impl Drop for ContextGuard {
    fn drop(&mut self) {
        if self.restore {
            let code = unsafe { (self.set)(self.previous as Ptr) };
            if code != 0 {
                tracing::error!(code, "device context restoration failed");
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Vendor {
    Cuda,
    Metax,
    Ascend,
}

pub struct NativeBackend {
    sdk: Library,
    // CUDA context selection is a driver operation, distinct from libcudart.
    _driver: Option<Library>,
    vendor: Vendor,
    identity: DeviceIdentity,
    count: u32,
    get_context: GetContext,
    set_context: SetContext,
}
impl std::fmt::Debug for NativeBackend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("NativeBackend")
            .field("identity", &self.identity)
            .finish()
    }
}

unsafe fn symbol<T: Copy>(library: &Library, name: &str, platform: &str) -> DeviceResult<T> {
    unsafe { library.get::<T>(name.as_bytes()).map(|s| *s) }.map_err(|e| PlatformError {
        code: "missing_symbol".into(),
        provider: platform.into(),
        message: format!("{name}: {e}"),
        retryable: false,
    })
}
fn load(name: &str, platform: &str) -> DeviceResult<Library> {
    // Only fixed vendor library names are accepted; selected by the caller.
    unsafe { Library::new(name) }.map_err(|e| PlatformError {
        code: "missing_sdk".into(),
        provider: platform.into(),
        message: format!("{name}: {e}"),
        retryable: false,
    })
}
impl NativeBackend {
    /// Select a device's SDK-managed context. This adapter never resets a device,
    /// finalizes a shared SDK, or destroys a context owned by an inference engine.
    /// The embedding application must not reset/finalize it while handles exist.
    pub fn open(platform: &str, device: u32) -> DeviceResult<Arc<Self>> {
        let (vendor, name, prefix) = match platform {
            "cuda" => (Vendor::Cuda, "libcudart.so.13", "cuda"),
            "metax" => (Vendor::Metax, "libmcruntime.so", "mc"),
            "ascend" => (Vendor::Ascend, "libascendcl.so", "aclrt"),
            _ => {
                return Err(PlatformError {
                    code: "unsupported_platform".into(),
                    provider: platform.into(),
                    message: "expected cuda, metax or ascend".into(),
                    retryable: false,
                });
            }
        };
        let sdk = load(name, platform)?;
        let driver = if vendor == Vendor::Cuda {
            Some(load("libcuda.so.1", platform)?)
        } else {
            None
        };
        let ctx_lib = driver.as_ref().unwrap_or(&sdk);
        let ctx_prefix = if vendor == Vendor::Cuda {
            "cuCtx"
        } else if vendor == Vendor::Metax {
            "mcCtx"
        } else {
            "aclrt"
        };
        let get_name = if vendor == Vendor::Ascend {
            "aclrtGetCurrentContext".to_owned()
        } else {
            format!("{ctx_prefix}GetCurrent")
        };
        let set_name = if vendor == Vendor::Ascend {
            "aclrtSetCurrentContext".to_owned()
        } else {
            format!("{ctx_prefix}SetCurrent")
        };
        let get_context: GetContext = unsafe { symbol(ctx_lib, &get_name, platform)? };
        let set_context: SetContext = unsafe { symbol(ctx_lib, &set_name, platform)? };
        let mut backend = Self {
            sdk,
            _driver: driver,
            vendor,
            identity: DeviceIdentity {
                platform: platform.into(),
                ordinal: device,
                context: 0,
            },
            count: 0,
            get_context,
            set_context,
        };
        unsafe {
            if vendor == Vendor::Ascend {
                let init: unsafe extern "C" fn(*const i8) -> i32 =
                    symbol(&backend.sdk, "aclInit", platform)?;
                let rc = init(std::ptr::null());
                if rc != 100002 {
                    backend.check("aclInit", rc)?;
                }
            }
            let get_count: unsafe extern "C" fn(*mut u32) -> i32 =
                symbol(&backend.sdk, &format!("{prefix}GetDeviceCount"), platform)?;
            let mut count = 0;
            backend.check("get_device_count", get_count(&mut count))?;
            if device >= count {
                return Err(backend.error(
                    "invalid_device",
                    format!("device {device} outside count {count}"),
                ));
            }
            backend.count = count;
            let mut previous = std::ptr::null_mut();
            // A fresh host thread may have no current context yet.
            let prior_rc = get_context(&mut previous);
            if prior_rc != 0 && !(vendor == Vendor::Ascend && prior_rc == 107002) {
                backend.check("get_previous_context", prior_rc)?;
            }
            let _restore = ContextGuard {
                set: set_context,
                previous: previous as usize,
                restore: !previous.is_null() || vendor != Vendor::Ascend,
            };
            let set_device: unsafe extern "C" fn(i32) -> i32 =
                symbol(&backend.sdk, &format!("{prefix}SetDevice"), platform)?;
            backend.check("set_device", set_device(device as i32))?;
            if vendor != Vendor::Ascend {
                let free: unsafe extern "C" fn(Ptr) -> i32 =
                    symbol(&backend.sdk, &format!("{prefix}Free"), platform)?;
                backend.check("initialize_primary_context", free(std::ptr::null_mut()))?;
            }
            let mut context = std::ptr::null_mut();
            backend.check("get_context", get_context(&mut context))?;
            if context.is_null() {
                return Err(backend.error("invalid_context", "SDK returned a null context"));
            }
            backend.identity.context = context as usize;
        }
        Ok(Arc::new(backend))
    }
    /// Free and total device memory in bytes, for allocator diagnostics.
    pub fn memory_info(&self) -> DeviceResult<(usize, usize)> {
        self.bound(|| unsafe {
            let mut free = 0;
            let mut total = 0;
            let rc = if self.vendor == Vendor::Ascend {
                let f: unsafe extern "C" fn(u32, *mut usize, *mut usize) -> i32 =
                    self.function("aclrtGetMemInfo")?;
                f(1, &mut free, &mut total) // ACL_HBM_MEM
            } else {
                let f: unsafe extern "C" fn(*mut usize, *mut usize) -> i32 =
                    self.function(&self.name("MemGetInfo"))?;
                f(&mut free, &mut total)
            };
            self.check("memory_info", rc)?;
            Ok((free, total))
        })
    }

    fn error(&self, code: &str, message: impl Into<String>) -> PlatformError {
        PlatformError {
            code: code.into(),
            provider: self.identity.platform.clone(),
            message: message.into(),
            retryable: false,
        }
    }
    fn check(&self, operation: &str, code: i32) -> DeviceResult<()> {
        if code == 0 {
            Ok(())
        } else {
            Err(self.error(
                "sdk_error",
                format!("{operation} returned native code {code}"),
            ))
        }
    }
    fn name(&self, suffix: &str) -> String {
        format!(
            "{}{}",
            match self.vendor {
                Vendor::Cuda => "cuda",
                Vendor::Metax => "mc",
                Vendor::Ascend => "aclrt",
            },
            suffix
        )
    }
    unsafe fn function<T: Copy>(&self, name: &str) -> DeviceResult<T> {
        unsafe { symbol(&self.sdk, name, &self.identity.platform) }
    }
    fn bound<T>(&self, work: impl FnOnce() -> DeviceResult<T>) -> DeviceResult<T> {
        let mut previous = std::ptr::null_mut();
        unsafe {
            let rc = (self.get_context)(&mut previous);
            // ACL reports "context pointer null" on new reclaimer threads.
            if rc != 0 && !(self.vendor == Vendor::Ascend && rc == 107002) {
                self.check("get_context", rc)?;
            }
            self.check(
                "bind_context",
                (self.set_context)(self.identity.context as Ptr),
            )?;
        }
        let _restore = ContextGuard {
            set: self.set_context,
            previous: previous as usize,
            restore: !previous.is_null() || self.vendor != Vendor::Ascend,
        };
        work()
    }

    unsafe fn handle_op(&self, suffix: &str, handle: usize) -> DeviceResult<()> {
        self.bound(|| unsafe {
            let f: unsafe extern "C" fn(Ptr) -> i32 = self.function(&self.name(suffix))?;
            self.check(suffix, f(handle as Ptr))
        })
    }
    unsafe fn query(&self, handle: usize, event: bool) -> DeviceResult<bool> {
        self.bound(|| unsafe {
            if self.vendor == Vendor::Ascend {
                let name = if event {
                    "aclrtQueryEventStatus"
                } else {
                    "aclrtStreamQuery"
                };
                let f: unsafe extern "C" fn(Ptr, *mut i32) -> i32 = self.function(name)?;
                let mut status = -1;
                self.check(name, f(handle as Ptr, &mut status))?;
                // Recorded event COMPLETE=1; stream COMPLETE=0.
                let complete = if event { 1 } else { 0 };
                if status != 0 && status != 1 {
                    return Err(self.error("invalid_status", format!("{name} status {status}")));
                }
                Ok(status == complete)
            } else {
                let f: unsafe extern "C" fn(Ptr) -> i32 =
                    self.function(&self.name(if event { "EventQuery" } else { "StreamQuery" }))?;
                match f(handle as Ptr) {
                    0 => Ok(true),
                    600 => Ok(false), // CUDA and MACA headers both define NotReady=600.
                    rc => {
                        self.check("query", rc)?;
                        Ok(false)
                    }
                }
            }
        })
    }
}

// SAFETY: Native handles are context-bound on every calling thread and only the
// selected SDK operates on them. Raw validity/exclusivity is the caller's contract.
unsafe impl DeviceBackend for NativeBackend {
    fn identity(&self) -> &DeviceIdentity {
        &self.identity
    }
    fn device_count(&self) -> u32 {
        self.count
    }
    fn device_copy_alignment(&self) -> usize {
        if self.vendor == Vendor::Ascend { 64 } else { 1 }
    }
    unsafe fn allocate(&self, size: usize, space: MemorySpace) -> DeviceResult<usize> {
        self.bound(|| unsafe {
            let mut ptr = std::ptr::null_mut();
            let name = self.name(if space == MemorySpace::Host {
                "MallocHost"
            } else {
                "Malloc"
            });
            let rc = if (self.vendor == Vendor::Metax && space == MemorySpace::Host)
                || (self.vendor == Vendor::Ascend && space == MemorySpace::Device)
            {
                let f: unsafe extern "C" fn(*mut Ptr, usize, u32) -> i32 = self.function(&name)?;
                // MACA host default=0; ACL normal-only device allocation=2.
                f(
                    &mut ptr,
                    size,
                    if self.vendor == Vendor::Ascend { 2 } else { 0 },
                )
            } else {
                let f: unsafe extern "C" fn(*mut Ptr, usize) -> i32 = self.function(&name)?;
                f(&mut ptr, size)
            };
            self.check(&name, rc)?;
            Ok(ptr as usize)
        })
    }
    unsafe fn release(&self, address: usize, space: MemorySpace) -> DeviceResult<()> {
        unsafe {
            self.handle_op(
                if space == MemorySpace::Host {
                    "FreeHost"
                } else {
                    "Free"
                },
                address,
            )
        }
    }
    unsafe fn register_host(&self, address: usize, size: usize) -> DeviceResult<()> {
        self.bound(|| unsafe {
            let name = if self.vendor == Vendor::Ascend {
                "aclrtHostRegisterV2".into()
            } else {
                self.name("HostRegister")
            };
            let f: unsafe extern "C" fn(Ptr, usize, u32) -> i32 = self.function(&name)?;
            self.check(
                &name,
                f(
                    address as Ptr,
                    size,
                    if self.vendor == Vendor::Ascend {
                        0x10000000
                    } else {
                        0
                    },
                ),
            )
        })
    }
    unsafe fn unregister_host(&self, address: usize) -> DeviceResult<()> {
        unsafe { self.handle_op("HostUnregister", address) }
    }
    unsafe fn create_stream(&self) -> DeviceResult<usize> {
        self.bound(|| unsafe {
            let mut stream = std::ptr::null_mut();
            if self.vendor == Vendor::Ascend {
                let f: unsafe extern "C" fn(*mut Ptr) -> i32 =
                    self.function("aclrtCreateStream")?;
                self.check("create_stream", f(&mut stream))?;
            } else {
                let f: unsafe extern "C" fn(*mut Ptr, u32) -> i32 =
                    self.function(&self.name("StreamCreateWithFlags"))?;
                self.check("create_stream", f(&mut stream, 1))?;
            }
            Ok(stream as usize)
        })
    }
    unsafe fn destroy_stream(&self, stream: usize) -> DeviceResult<()> {
        unsafe {
            self.handle_op(
                if self.vendor == Vendor::Ascend {
                    "DestroyStream"
                } else {
                    "StreamDestroy"
                },
                stream,
            )
        }
    }
    unsafe fn create_event(&self) -> DeviceResult<usize> {
        self.bound(|| unsafe {
            let mut event = std::ptr::null_mut();
            if self.vendor == Vendor::Ascend {
                let f: unsafe extern "C" fn(*mut Ptr) -> i32 = self.function("aclrtCreateEvent")?;
                self.check("create_event", f(&mut event))?;
            } else {
                let f: unsafe extern "C" fn(*mut Ptr, u32) -> i32 =
                    self.function(&self.name("EventCreateWithFlags"))?;
                self.check("create_event", f(&mut event, 2))?;
            }
            Ok(event as usize)
        })
    }
    unsafe fn destroy_event(&self, event: usize) -> DeviceResult<()> {
        unsafe {
            self.handle_op(
                if self.vendor == Vendor::Ascend {
                    "DestroyEvent"
                } else {
                    "EventDestroy"
                },
                event,
            )
        }
    }
    unsafe fn record_event(&self, event: usize, stream: usize) -> DeviceResult<()> {
        self.bound(|| unsafe {
            let f: unsafe extern "C" fn(Ptr, Ptr) -> i32 =
                self.function(&self.name(if self.vendor == Vendor::Ascend {
                    "RecordEvent"
                } else {
                    "EventRecord"
                }))?;
            self.check("record_event", f(event as Ptr, stream as Ptr))
        })
    }
    unsafe fn query_event(&self, event: usize) -> DeviceResult<bool> {
        unsafe { self.query(event, true) }
    }
    unsafe fn query_stream(&self, stream: usize) -> DeviceResult<bool> {
        unsafe { self.query(stream, false) }
    }
    unsafe fn copy(
        &self,
        dst: usize,
        src: usize,
        size: usize,
        kind: u32,
        stream: usize,
    ) -> DeviceResult<()> {
        self.bound(|| unsafe {
            let name = self.name("MemcpyAsync");
            let rc = if self.vendor == Vendor::Ascend {
                let f: unsafe extern "C" fn(Ptr, usize, *const c_void, usize, u32, Ptr) -> i32 =
                    self.function(&name)?;
                f(
                    dst as Ptr,
                    size,
                    src as *const c_void,
                    size,
                    kind,
                    stream as Ptr,
                )
            } else {
                let f: unsafe extern "C" fn(Ptr, *const c_void, usize, u32, Ptr) -> i32 =
                    self.function(&name)?;
                f(dst as Ptr, src as *const c_void, size, kind, stream as Ptr)
            };
            self.check(&name, rc)
        })
    }
}
