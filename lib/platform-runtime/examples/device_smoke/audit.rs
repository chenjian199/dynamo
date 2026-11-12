// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Test-only counters of successful calls to the real vendor SDK backend.
use dynamo_platform::device::{DeviceBackend, DeviceIdentity, DeviceResult, MemorySpace};
use dynamo_platform_runtime::NativeBackend;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Debug)]
pub struct AuditedBackend {
    pub native: Arc<NativeBackend>,
    // allocation/free, registration/unregistration, stream create/destroy, event create/destroy
    counts: [AtomicUsize; 8],
    copies: AtomicUsize,
}
impl AuditedBackend {
    pub fn new(native: Arc<NativeBackend>) -> Arc<Self> {
        Arc::new(Self {
            native,
            counts: std::array::from_fn(|_| AtomicUsize::new(0)),
            copies: AtomicUsize::new(0),
        })
    }
    pub fn snapshot(&self) -> [usize; 8] {
        std::array::from_fn(|i| self.counts[i].load(Ordering::SeqCst))
    }
    pub fn completed_submissions(&self) -> usize {
        self.copies.load(Ordering::SeqCst)
    }
}

// SAFETY: Every raw operation delegates unchanged to NativeBackend; counts advance
// only on success. Atomic counters allow the smoke's cross-thread context case.
unsafe impl DeviceBackend for AuditedBackend {
    fn identity(&self) -> &DeviceIdentity {
        self.native.identity()
    }
    fn device_count(&self) -> u32 {
        self.native.device_count()
    }
    fn device_copy_alignment(&self) -> usize {
        self.native.device_copy_alignment()
    }
    unsafe fn allocate(&self, size: usize, space: MemorySpace) -> DeviceResult<usize> {
        let result = unsafe { self.native.allocate(size, space)? };
        self.counts[0].fetch_add(1, Ordering::SeqCst);
        Ok(result)
    }
    unsafe fn release(&self, address: usize, space: MemorySpace) -> DeviceResult<()> {
        unsafe { self.native.release(address, space)? };
        self.counts[1].fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn register_host(&self, address: usize, size: usize) -> DeviceResult<()> {
        unsafe { self.native.register_host(address, size)? };
        self.counts[2].fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn unregister_host(&self, address: usize) -> DeviceResult<()> {
        unsafe { self.native.unregister_host(address)? };
        self.counts[3].fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn create_stream(&self) -> DeviceResult<usize> {
        let result = unsafe { self.native.create_stream()? };
        self.counts[4].fetch_add(1, Ordering::SeqCst);
        Ok(result)
    }
    unsafe fn destroy_stream(&self, stream: usize) -> DeviceResult<()> {
        unsafe { self.native.destroy_stream(stream)? };
        self.counts[5].fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn create_event(&self) -> DeviceResult<usize> {
        let result = unsafe { self.native.create_event()? };
        self.counts[6].fetch_add(1, Ordering::SeqCst);
        Ok(result)
    }
    unsafe fn destroy_event(&self, event: usize) -> DeviceResult<()> {
        unsafe { self.native.destroy_event(event)? };
        self.counts[7].fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn record_event(&self, event: usize, stream: usize) -> DeviceResult<()> {
        unsafe { self.native.record_event(event, stream) }
    }
    unsafe fn query_event(&self, event: usize) -> DeviceResult<bool> {
        unsafe { self.native.query_event(event) }
    }
    unsafe fn query_stream(&self, stream: usize) -> DeviceResult<bool> {
        unsafe { self.native.query_stream(stream) }
    }
    unsafe fn copy(
        &self,
        dst: usize,
        src: usize,
        size: usize,
        kind: u32,
        stream: usize,
    ) -> DeviceResult<()> {
        unsafe { self.native.copy(dst, src, size, kind, stream)? };
        self.copies.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}
