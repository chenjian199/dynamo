// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! SDK-independent device operations. SDK handles never cross provider boundaries.

use crate::PlatformError;

/// Address-space identity, including the context selected by the provider.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeviceIdentity {
    pub platform: String,
    pub ordinal: u32,
    pub context: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MemorySpace {
    Device,
    Host,
}

pub type DeviceResult<T> = Result<T, PlatformError>;

/// Raw operations implemented by a vendor SDK adapter.
///
/// # Safety
/// Implementations must bind the correct context on *each calling thread*, preserve
/// the caller's context, and report actual completion. Handles must remain valid
/// until their matching release. Errors must never be reported as completion.
/// Implementations must not unwind after accepting asynchronous work.
/// Callers of raw methods must ensure handle validity and exclusive memory access.
/// SDK contexts must not be reset/finalized, or reused after fork, while handles live.
/// For SDKs that cannot restore an unbound thread, first use may leave it bound;
/// an existing non-null caller context must always be restored.
pub unsafe trait DeviceBackend: std::fmt::Debug + Send + Sync + 'static {
    fn identity(&self) -> &DeviceIdentity;
    fn device_count(&self) -> u32;
    /// # Safety
    /// Size must be positive and representable as isize; release exactly once.
    unsafe fn allocate(&self, size: usize, space: MemorySpace) -> DeviceResult<usize>;
    /// # Safety
    /// Address must be an owned allocation of this space; all accesses must have finished.
    unsafe fn release(&self, address: usize, space: MemorySpace) -> DeviceResult<()>;
    /// # Safety
    /// The entire range must be valid and unregistered, and retained until unregister.
    unsafe fn register_host(&self, address: usize, size: usize) -> DeviceResult<()>;
    /// # Safety
    /// Address must have a live registration here; all DMA must have finished.
    unsafe fn unregister_host(&self, address: usize) -> DeviceResult<()>;
    /// # Safety
    /// The SDK context must remain alive until this stream is destroyed.
    unsafe fn create_stream(&self) -> DeviceResult<usize>;
    /// # Safety
    /// Stream must be owned here, unused and destroyed exactly once.
    unsafe fn destroy_stream(&self, stream: usize) -> DeviceResult<()>;
    /// # Safety
    /// The SDK context must remain alive until this event is destroyed.
    unsafe fn create_event(&self) -> DeviceResult<usize>;
    /// # Safety
    /// Event must be owned here and no longer referenced by queued operations.
    unsafe fn destroy_event(&self, event: usize) -> DeviceResult<()>;
    /// # Safety
    /// Event and stream must be live in this context; do not re-record in-flight events.
    unsafe fn record_event(&self, event: usize, stream: usize) -> DeviceResult<()>;
    /// # Safety
    /// Event must be live and recorded in this context.
    unsafe fn query_event(&self, event: usize) -> DeviceResult<bool>;
    /// # Safety
    /// Stream must be live in this context.
    unsafe fn query_stream(&self, stream: usize) -> DeviceResult<bool>;
    /// `kind` is H2D=1, D2H=2 or D2D=3; host buffers must be pinned/registered.
    /// # Safety
    /// Ranges and stream must belong here and remain valid, pinned where required, and free of conflicting accesses until completion.
    unsafe fn copy(
        &self,
        dst: usize,
        src: usize,
        size: usize,
        kind: u32,
        stream: usize,
    ) -> DeviceResult<()>;
    /// Nonzero alignment needed for a device-to-device copy's two addresses.
    fn device_copy_alignment(&self) -> usize {
        1
    }
}
