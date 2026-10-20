// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Owned and borrowed memory with bounded, non-blocking cancellation reclamation.
//! Allocation and SDK operations are synchronous; use a blocking worker from async code.

use crate::PlatformError;
use crate::device::{DeviceBackend, DeviceIdentity, DeviceResult, MemorySpace};
use std::any::Any;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

fn error(backend: &dyn DeviceBackend, code: &str, message: impl Into<String>) -> PlatformError {
    PlatformError {
        code: code.into(),
        provider: backend.identity().platform.clone(),
        message: message.into(),
        retryable: code == "busy",
    }
}

type Owner = Arc<dyn Any + Send + Sync>;

#[derive(Clone)]
pub struct Allocation(Arc<AllocationInner>);
struct AllocationInner {
    backend: Arc<dyn DeviceBackend>,
    address: usize,
    size: usize,
    space: MemorySpace,
    // Registered borrowed memory must unregister before releasing its owner.
    owned: bool,
    registered: bool,
    _owner: Option<Owner>,
}
impl std::fmt::Debug for Allocation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Allocation")
            .field("identity", self.identity())
            .field("size", &self.len())
            .field("space", &self.space())
            .finish()
    }
}
impl Allocation {
    pub fn address(&self) -> usize {
        self.0.address
    }
    pub fn len(&self) -> usize {
        self.0.size
    }
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
    pub fn space(&self) -> MemorySpace {
        self.0.space
    }
    pub fn identity(&self) -> &DeviceIdentity {
        self.0.backend.identity()
    }
    fn range(&self, offset: usize, count: usize) -> DeviceResult<usize> {
        if count == 0 || offset.checked_add(count).is_none_or(|end| end > self.len()) {
            return Err(error(
                self.0.backend.as_ref(),
                "invalid_range",
                "empty or out-of-bounds memory range",
            ));
        }
        self.address()
            .checked_add(offset)
            .ok_or_else(|| error(self.0.backend.as_ref(), "invalid_range", "address overflow"))
    }
    /// # Safety
    /// No DMA or other readers/writers may access this range concurrently.
    pub unsafe fn write(&self, offset: usize, data: &[u8]) -> DeviceResult<()> {
        if self.space() != MemorySpace::Host {
            return Err(error(
                self.0.backend.as_ref(),
                "wrong_space",
                "host access to device allocation",
            ));
        }
        let ptr = self.range(offset, data.len())?;
        unsafe {
            std::ptr::copy_nonoverlapping(data.as_ptr(), ptr as *mut u8, data.len());
        }
        Ok(())
    }
    /// # Safety
    /// The range must be initialized and no DMA/writer may modify it concurrently.
    pub unsafe fn read(&self, offset: usize, data: &mut [u8]) -> DeviceResult<()> {
        if self.space() != MemorySpace::Host {
            return Err(error(
                self.0.backend.as_ref(),
                "wrong_space",
                "host access to device allocation",
            ));
        }
        let ptr = self.range(offset, data.len())?;
        unsafe {
            std::ptr::copy_nonoverlapping(ptr as *const u8, data.as_mut_ptr(), data.len());
        }
        Ok(())
    }
}
impl Drop for AllocationInner {
    fn drop(&mut self) {
        let result = unsafe {
            if self.registered {
                self.backend.unregister_host(self.address)
            } else if self.owned {
                self.backend.release(self.address, self.space)
            } else {
                Ok(())
            }
        };
        if let Err(error) = result {
            // An unsuccessful unregister must not allow a borrowed owner to be freed.
            if let Some(owner) = self._owner.take() {
                std::mem::forget(owner);
            }
            std::mem::forget(self.backend.clone());
            tracing::error!(%error, "device memory release failed; ownership quarantined");
        }
    }
}

#[derive(Clone)]
pub struct ExecutionStream(Arc<StreamInner>);
struct StreamInner {
    backend: Arc<dyn DeviceBackend>,
    raw: usize,
    owned: bool,
    _owner: Option<Owner>,
}
impl ExecutionStream {
    pub fn identity(&self) -> &DeviceIdentity {
        self.0.backend.identity()
    }
    pub fn raw_handle(&self) -> usize {
        self.0.raw
    }
}
impl Drop for StreamInner {
    fn drop(&mut self) {
        if self.owned
            && let Err(error) = unsafe { self.backend.destroy_stream(self.raw) }
        {
            std::mem::forget(self.backend.clone());
            tracing::error!(%error, "device stream release failed");
        }
    }
}

struct Pending {
    stream: ExecutionStream,
    event: usize,
    // Keep both allocations (including registration leases/owners) until completion.
    _source: Allocation,
    _destination: Allocation,
    result: Arc<CompletionState>,
    stream_fallback: bool,
}
struct CompletionState {
    result: Mutex<Option<DeviceResult<()>>>,
    ready: Condvar,
}
struct Queue {
    pending: Mutex<Vec<Pending>>,
    // Reservation and close use sequential consistency across these two atomics:
    // close must not miss a reservation whose submitter also misses closed=true.
    in_flight: AtomicUsize,
    closed: AtomicBool,
    limit: usize,
}
struct Reservation {
    queue: Arc<Queue>,
    transferred: bool,
}
impl Drop for Reservation {
    fn drop(&mut self) {
        if !self.transferred {
            self.queue.in_flight.fetch_sub(1, Ordering::SeqCst);
        }
    }
}

/// A receipt for actual device completion. Dropping it cancels observation, not DMA.
/// The runtime reaper retains all resources until the SDK confirms completion.
pub struct CompletionEvent {
    state: Arc<CompletionState>,
}
impl CompletionEvent {
    pub fn query(&self) -> DeviceResult<bool> {
        let state = self.state.result.lock().unwrap_or_else(|e| e.into_inner());
        match state.as_ref() {
            None => Ok(false),
            Some(Ok(())) => Ok(true),
            Some(Err(e)) => Err(e.clone()),
        }
    }
    /// Bounded blocking wait. Call from a blocking worker, not a Tokio executor thread.
    pub fn wait(&self, timeout: Duration) -> DeviceResult<bool> {
        let state = self.state.result.lock().unwrap_or_else(|e| e.into_inner());
        let (state, _) = self
            .state
            .ready
            .wait_timeout_while(state, timeout, |r| r.is_none())
            .unwrap_or_else(|e| e.into_inner());
        match state.as_ref() {
            None => Ok(false),
            Some(Ok(())) => Ok(true),
            Some(Err(e)) => Err(e.clone()),
        }
    }
}

/// One selected provider/context, with at most `max_in_flight` outstanding copies.
#[derive(Clone)]
pub struct DeviceRuntime {
    backend: Arc<dyn DeviceBackend>,
    queue: Arc<Queue>,
}
impl DeviceRuntime {
    pub fn new(backend: Arc<dyn DeviceBackend>, max_in_flight: usize) -> DeviceResult<Self> {
        if max_in_flight == 0 {
            return Err(error(
                backend.as_ref(),
                "invalid_limit",
                "max_in_flight must be positive",
            ));
        }
        let queue = Arc::new(Queue {
            pending: Mutex::new(Vec::new()),
            in_flight: AtomicUsize::new(0),
            closed: AtomicBool::new(false),
            limit: max_in_flight,
        });
        let worker = queue.clone();
        std::thread::Builder::new()
            .name("device-reclaimer".into())
            .spawn(move || reclaim(worker))
            .map_err(|e| error(backend.as_ref(), "worker_start", e.to_string()))?;
        Ok(Self { backend, queue })
    }
    pub fn identity(&self) -> &DeviceIdentity {
        self.backend.identity()
    }
    pub fn device_count(&self) -> u32 {
        self.backend.device_count()
    }
    /// Maximum number of retained asynchronous operations across runtime clones.
    pub fn max_in_flight(&self) -> usize {
        self.queue.limit
    }

    pub fn allocate(&self, size: usize, space: MemorySpace) -> DeviceResult<Allocation> {
        if size == 0 || size > isize::MAX as usize {
            return Err(error(
                self.backend.as_ref(),
                "invalid_size",
                "allocation size must be in 1..=isize::MAX",
            ));
        }
        let address = unsafe { self.backend.allocate(size, space)? };
        if address == 0 {
            return Err(error(
                self.backend.as_ref(),
                "invalid_pointer",
                "SDK returned a null allocation",
            ));
        }
        Ok(Allocation(Arc::new(AllocationInner {
            backend: self.backend.clone(),
            address,
            size,
            space,
            owned: true,
            registered: false,
            _owner: None,
        })))
    }
    /// Import externally owned memory, optionally registering host pages for DMA.
    /// # Safety
    /// The owner must keep the entire range valid and exclusively managed until its
    /// last reference drops. Device memory must belong to this exact context. Host
    /// registration requires an unregistered range; otherwise host memory must
    /// already be pinned and its registration retained by `owner`.
    pub unsafe fn borrow(
        &self,
        address: usize,
        size: usize,
        space: MemorySpace,
        owner: Owner,
        register: bool,
    ) -> DeviceResult<Allocation> {
        if address == 0
            || size == 0
            || size > isize::MAX as usize
            || address.checked_add(size).is_none()
        {
            return Err(error(
                self.backend.as_ref(),
                "invalid_range",
                "invalid borrowed allocation",
            ));
        }
        if register && space != MemorySpace::Host {
            return Err(error(
                self.backend.as_ref(),
                "wrong_space",
                "only host pages may be registered",
            ));
        }
        if register {
            unsafe {
                self.backend.register_host(address, size)?;
            }
        }
        Ok(Allocation(Arc::new(AllocationInner {
            backend: self.backend.clone(),
            address,
            size,
            space,
            owned: false,
            registered: register,
            _owner: Some(owner),
        })))
    }
    pub fn create_stream(&self) -> DeviceResult<ExecutionStream> {
        let raw = unsafe { self.backend.create_stream()? };
        Ok(ExecutionStream(Arc::new(StreamInner {
            backend: self.backend.clone(),
            raw,
            owned: true,
            _owner: None,
        })))
    }
    /// # Safety
    /// `raw` must be a stream in this context; `owner` must retain it. All work
    /// submitted outside this API must finish before the owner's final release.
    pub unsafe fn borrow_stream(&self, raw: usize, owner: Owner) -> ExecutionStream {
        ExecutionStream(Arc::new(StreamInner {
            backend: self.backend.clone(),
            raw,
            owned: false,
            _owner: Some(owner),
        }))
    }
    /// Queue a checked H2D, D2H or same-context D2D copy.
    /// # Safety
    /// No conflicting host, engine, or device accesses may occur until completion.
    /// Dropping the receipt or receiving an SDK error does not relax this
    /// requirement: wait for `drain`/`close` to return true before reusing memory.
    #[allow(clippy::too_many_arguments)]
    pub unsafe fn copy_async(
        &self,
        stream: &ExecutionStream,
        dst: &Allocation,
        dst_offset: usize,
        src: &Allocation,
        src_offset: usize,
        size: usize,
    ) -> DeviceResult<CompletionEvent> {
        if [stream.identity(), src.identity(), dst.identity()]
            .iter()
            .any(|i| *i != self.identity())
        {
            return Err(error(
                self.backend.as_ref(),
                "wrong_context",
                "cross-platform/context/device copies require an explicit P2P provider",
            ));
        }
        let dst_ptr = dst.range(dst_offset, size)?;
        let src_ptr = src.range(src_offset, size)?;
        let kind = match (src.space(), dst.space()) {
            (MemorySpace::Host, MemorySpace::Device) => 1,
            (MemorySpace::Device, MemorySpace::Host) => 2,
            (MemorySpace::Device, MemorySpace::Device) => 3,
            _ => {
                return Err(error(
                    self.backend.as_ref(),
                    "unsupported",
                    "host-to-host copy is not a device operation",
                ));
            }
        };
        if kind == 3 {
            let alignment = self.backend.device_copy_alignment();
            if !src_ptr.is_multiple_of(alignment) || !dst_ptr.is_multiple_of(alignment) {
                return Err(error(
                    self.backend.as_ref(),
                    "alignment",
                    "device copy address alignment is invalid",
                ));
            }
            if src_ptr < dst_ptr.saturating_add(size) && dst_ptr < src_ptr.saturating_add(size) {
                return Err(error(
                    self.backend.as_ref(),
                    "overlap",
                    "overlapping device copy is invalid",
                ));
            }
        }
        if self
            .queue
            .in_flight
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| {
                (n < self.queue.limit).then_some(n + 1)
            })
            .is_err()
        {
            return Err(error(
                self.backend.as_ref(),
                "busy",
                "in-flight copy limit reached",
            ));
        }
        let mut reservation = Reservation {
            queue: self.queue.clone(),
            transferred: false,
        };
        if self.queue.closed.load(Ordering::SeqCst) {
            return Err(error(
                self.backend.as_ref(),
                "closed",
                "runtime no longer accepts copies",
            ));
        }
        let event = unsafe { self.backend.create_event()? };
        let state = Arc::new(CompletionState {
            result: Mutex::new(None),
            ready: Condvar::new(),
        });
        let submission = unsafe {
            self.backend
                .copy(dst_ptr, src_ptr, size, kind, stream.raw_handle())
        }
        .and_then(|()| unsafe { self.backend.record_event(event, stream.raw_handle()) });
        // Even a failed submission can have queued work. Retain ownership until
        // the *stream* is confirmed idle instead of querying an unrecorded event.
        let failed = submission.is_err();
        if let Err(ref e) = submission {
            *state.result.lock().unwrap_or_else(|e| e.into_inner()) = Some(Err(e.clone()));
        }
        self.queue
            .pending
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push(Pending {
                stream: stream.clone(),
                event,
                _source: src.clone(),
                _destination: dst.clone(),
                result: state.clone(),
                stream_fallback: failed,
            });
        // Ownership of the occupied slot transfers to the reclaimer.
        reservation.transferred = true;
        submission?;
        Ok(CompletionEvent { state })
    }
    /// Stop accepting copies across all clones, then wait for retirement.
    /// Idempotent; a timeout retains resources and a later close can retry waiting.
    pub fn close(&self, timeout: Duration) -> bool {
        self.queue.closed.store(true, Ordering::SeqCst);
        self.drain(timeout)
    }
    /// Wait for all submitted operations, including cancelled receipts, to retire.
    /// A device failure may quarantine resources; timeout then reports `false`.
    pub fn drain(&self, timeout: Duration) -> bool {
        let start = Instant::now();
        loop {
            if self.queue.in_flight.load(Ordering::SeqCst) == 0 {
                return true;
            }
            if start.elapsed() >= timeout {
                return false;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    }
}

fn reclaim(queue: Arc<Queue>) {
    loop {
        {
            let mut pending =
                std::mem::take(&mut *queue.pending.lock().unwrap_or_else(|e| e.into_inner()));
            let mut index = 0;
            while index < pending.len() {
                let p = &mut pending[index];
                let backend = &p.stream.0.backend;
                let status = unsafe {
                    if p.stream_fallback {
                        backend.query_stream(p.stream.raw_handle())
                    } else {
                        backend.query_event(p.event)
                    }
                };
                match status {
                    Ok(true) => {
                        let p = pending.swap_remove(index);
                        if let Err(error) = unsafe { p.stream.0.backend.destroy_event(p.event) } {
                            std::mem::forget(p.stream.0.backend.clone());
                            tracing::error!(%error, "completion event release failed");
                        }
                        // Drop memory/registration leases BEFORE publishing retirement.
                        let state = p.result.clone();
                        drop(p);
                        let mut result = state.result.lock().unwrap_or_else(|e| e.into_inner());
                        if result.is_none() {
                            *result = Some(Ok(()));
                        }
                        state.ready.notify_all();
                        queue.in_flight.fetch_sub(1, Ordering::SeqCst);
                    }
                    Ok(false) => {
                        index += 1;
                    }
                    Err(e) => {
                        let mut result = p.result.result.lock().unwrap_or_else(|e| e.into_inner());
                        if result.is_none() {
                            tracing::error!(error = %e, "device completion failed; resources retained until stream is idle");
                            *result = Some(Err(e));
                            p.result.ready.notify_all();
                        }
                        p.stream_fallback = true;
                        index += 1;
                    }
                }
            }
            queue
                .pending
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .extend(pending);
            if queue.in_flight.load(Ordering::SeqCst) == 0 && Arc::strong_count(&queue) == 1 {
                return;
            }
        }
        std::thread::sleep(Duration::from_millis(1));
    }
}
