// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

use super::memory::*;
use crate::PlatformError;
use crate::device::*;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Barrier, Mutex};
use std::time::Duration;

#[derive(Debug)]
struct Fake {
    identity: DeviceIdentity,
    ready: AtomicBool,
    fail_record: AtomicBool,
    fail_query: AtomicBool,
    allocations: Mutex<HashMap<usize, Vec<u8>>>,
    released: AtomicUsize,
    registered: AtomicUsize,
    streams_destroyed: AtomicUsize,
    submission_barrier: Mutex<Option<Arc<Barrier>>>,
}
impl Fake {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            identity: DeviceIdentity {
                platform: "test".into(),
                ordinal: 0,
                context: 1,
            },
            ready: AtomicBool::new(false),
            fail_record: AtomicBool::new(false),
            fail_query: AtomicBool::new(false),
            allocations: Mutex::new(HashMap::new()),
            released: AtomicUsize::new(0),
            registered: AtomicUsize::new(0),
            streams_destroyed: AtomicUsize::new(0),
            submission_barrier: Mutex::new(None),
        })
    }
    fn failure(&self) -> PlatformError {
        PlatformError {
            code: "injected".into(),
            provider: "test".into(),
            message: "test failure".into(),
            retryable: false,
        }
    }
}
// SAFETY: The fake owns real host buffers; completion is controlled by the test.
// No actual deferred memory accesses are performed after copy().
unsafe impl DeviceBackend for Fake {
    fn identity(&self) -> &DeviceIdentity {
        &self.identity
    }
    fn device_count(&self) -> u32 {
        1
    }
    unsafe fn allocate(&self, size: usize, _: MemorySpace) -> DeviceResult<usize> {
        let mut data = vec![0; size];
        let ptr = data.as_mut_ptr() as usize;
        self.allocations.lock().unwrap().insert(ptr, data);
        Ok(ptr)
    }
    unsafe fn release(&self, ptr: usize, _: MemorySpace) -> DeviceResult<()> {
        assert!(self.allocations.lock().unwrap().remove(&ptr).is_some());
        self.released.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn register_host(&self, _: usize, _: usize) -> DeviceResult<()> {
        self.registered.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn unregister_host(&self, _: usize) -> DeviceResult<()> {
        self.registered.fetch_sub(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn create_stream(&self) -> DeviceResult<usize> {
        Ok(1)
    }
    unsafe fn destroy_stream(&self, _: usize) -> DeviceResult<()> {
        self.streams_destroyed.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    unsafe fn create_event(&self) -> DeviceResult<usize> {
        Ok(1)
    }
    unsafe fn destroy_event(&self, _: usize) -> DeviceResult<()> {
        Ok(())
    }
    unsafe fn record_event(&self, _: usize, _: usize) -> DeviceResult<()> {
        if self.fail_record.load(Ordering::SeqCst) {
            Err(self.failure())
        } else {
            Ok(())
        }
    }
    unsafe fn query_event(&self, _: usize) -> DeviceResult<bool> {
        if self.fail_query.load(Ordering::SeqCst) {
            Err(self.failure())
        } else {
            Ok(self.ready.load(Ordering::SeqCst))
        }
    }
    unsafe fn query_stream(&self, _: usize) -> DeviceResult<bool> {
        Ok(self.ready.load(Ordering::SeqCst))
    }
    unsafe fn copy(
        &self,
        dst: usize,
        src: usize,
        size: usize,
        _: u32,
        _: usize,
    ) -> DeviceResult<()> {
        let barrier = self.submission_barrier.lock().unwrap().clone();
        if let Some(barrier) = barrier {
            barrier.wait();
            barrier.wait();
        }
        unsafe { std::ptr::copy_nonoverlapping(src as *const u8, dst as *mut u8, size) };
        Ok(())
    }
}

#[test]
fn cancellation_retains_allocations_and_applies_backpressure() {
    let backend = Fake::new();
    let runtime = DeviceRuntime::new(backend.clone(), 1).unwrap();
    let stream = runtime.create_stream().unwrap();
    let src = runtime.allocate(256, MemorySpace::Host).unwrap();
    let dst = runtime.allocate(256, MemorySpace::Device).unwrap();
    let event = unsafe { runtime.copy_async(&stream, &dst, 0, &src, 0, 256) }.unwrap();
    assert!(!event.query().unwrap());
    assert!(!event.wait(Duration::from_millis(2)).unwrap());
    assert_eq!(
        unsafe { runtime.copy_async(&stream, &dst, 0, &src, 0, 256) }
            .err()
            .unwrap()
            .code,
        "busy"
    );
    drop(event);
    drop(src);
    drop(dst);
    drop(stream);
    assert!(!runtime.drain(Duration::from_millis(2)));
    assert_eq!(backend.released.load(Ordering::SeqCst), 0);
    backend.ready.store(true, Ordering::SeqCst);
    assert!(runtime.drain(Duration::from_secs(2)));
    assert_eq!(backend.released.load(Ordering::SeqCst), 2);
    assert_eq!(backend.streams_destroyed.load(Ordering::SeqCst), 1);
    assert!(runtime.drain(Duration::ZERO));
}

#[test]
fn submission_and_completion_errors_require_stream_retirement() {
    for record_failure in [true, false] {
        let backend = Fake::new();
        backend.fail_record.store(record_failure, Ordering::SeqCst);
        backend.fail_query.store(!record_failure, Ordering::SeqCst);
        let runtime = DeviceRuntime::new(backend.clone(), 1).unwrap();
        let stream = runtime.create_stream().unwrap();
        let src = runtime.allocate(256, MemorySpace::Host).unwrap();
        let dst = runtime.allocate(256, MemorySpace::Device).unwrap();
        let result = unsafe { runtime.copy_async(&stream, &dst, 0, &src, 0, 256) };
        if record_failure {
            assert!(result.is_err());
        } else {
            assert!(result.unwrap().wait(Duration::from_secs(2)).is_err());
        }
        drop(src);
        drop(dst);
        drop(stream);
        assert!(!runtime.drain(Duration::from_millis(2)));
        assert_eq!(backend.released.load(Ordering::SeqCst), 0);
        backend.ready.store(true, Ordering::SeqCst);
        assert!(runtime.drain(Duration::from_secs(2)));
        assert_eq!(backend.released.load(Ordering::SeqCst), 2);
    }
}

#[test]
fn borrowed_memory_and_stream_keep_their_owners_without_freeing_handles() {
    let backend = Fake::new();
    let runtime = DeviceRuntime::new(backend.clone(), 1).unwrap();
    let owner = Arc::new(vec![7u8; 256]);
    let weak = Arc::downgrade(&owner);
    let host = unsafe {
        runtime.borrow(
            owner.as_ptr() as usize,
            owner.len(),
            MemorySpace::Host,
            owner.clone(),
            true,
        )
    }
    .unwrap();
    let stream = unsafe { runtime.borrow_stream(123, owner.clone()) };
    let dst = runtime.allocate(256, MemorySpace::Device).unwrap();
    let event = unsafe { runtime.copy_async(&stream, &dst, 0, &host, 0, 256) }.unwrap();
    drop(owner);
    drop(host);
    drop(stream);
    drop(event);
    drop(dst);
    assert!(weak.upgrade().is_some());
    assert_eq!(backend.registered.load(Ordering::SeqCst), 1);
    backend.ready.store(true, Ordering::SeqCst);
    assert!(runtime.drain(Duration::from_secs(2)));
    assert!(weak.upgrade().is_none());
    assert_eq!(backend.registered.load(Ordering::SeqCst), 0);
    assert_eq!(backend.streams_destroyed.load(Ordering::SeqCst), 0);
    assert_eq!(backend.released.load(Ordering::SeqCst), 1);
}

#[test]
fn dropping_runtime_does_not_cancel_in_flight_ownership() {
    let backend = Fake::new();
    let runtime = DeviceRuntime::new(backend.clone(), 1).unwrap();
    let stream = runtime.create_stream().unwrap();
    let src = runtime.allocate(256, MemorySpace::Host).unwrap();
    let dst = runtime.allocate(256, MemorySpace::Device).unwrap();
    let event = unsafe { runtime.copy_async(&stream, &dst, 0, &src, 0, 256) }.unwrap();
    drop(src);
    drop(dst);
    drop(stream);
    drop(runtime);
    assert_eq!(backend.released.load(Ordering::SeqCst), 0);
    backend.ready.store(true, Ordering::SeqCst);
    assert!(event.wait(Duration::from_secs(2)).unwrap());
    assert_eq!(backend.released.load(Ordering::SeqCst), 2);
}

#[test]
fn close_is_idempotent_and_rejects_new_work() {
    let backend = Fake::new();
    let runtime = DeviceRuntime::new(backend.clone(), 1).unwrap();
    let stream = runtime.create_stream().unwrap();
    let src = runtime.allocate(256, MemorySpace::Host).unwrap();
    let dst = runtime.allocate(256, MemorySpace::Device).unwrap();
    assert!(runtime.close(Duration::ZERO));
    assert!(runtime.close(Duration::ZERO));
    assert_eq!(
        unsafe { runtime.copy_async(&stream, &dst, 0, &src, 0, 256) }
            .err()
            .unwrap()
            .code,
        "closed"
    );
    assert!(runtime.drain(Duration::ZERO));
}

#[test]
fn foreign_context_and_overflow_are_rejected_before_submission() {
    let backend = Fake::new();
    let runtime = DeviceRuntime::new(backend.clone(), 1).unwrap();
    let mut foreign = Fake::new();
    Arc::get_mut(&mut foreign).unwrap().identity.context = 2;
    let other = DeviceRuntime::new(foreign, 1).unwrap();
    let stream = runtime.create_stream().unwrap();
    let src = runtime.allocate(256, MemorySpace::Host).unwrap();
    let dst = other.allocate(256, MemorySpace::Device).unwrap();
    assert_eq!(
        unsafe { runtime.copy_async(&stream, &dst, 0, &src, 0, 256) }
            .err()
            .unwrap()
            .code,
        "wrong_context"
    );
    let dst = runtime.allocate(256, MemorySpace::Device).unwrap();
    assert_eq!(
        unsafe { runtime.copy_async(&stream, &dst, usize::MAX, &src, 0, 256) }
            .err()
            .unwrap()
            .code,
        "invalid_range"
    );
    assert!(runtime.drain(Duration::ZERO));
}

#[test]
fn close_waits_for_reserved_but_not_yet_queued_submissions() {
    let backend = Fake::new();
    let barrier = Arc::new(Barrier::new(2));
    *backend.submission_barrier.lock().unwrap() = Some(barrier.clone());
    let runtime = DeviceRuntime::new(backend.clone(), 1).unwrap();
    let submitter = runtime.clone();
    let job = std::thread::spawn(move || {
        let stream = submitter.create_stream().unwrap();
        let src = submitter.allocate(256, MemorySpace::Host).unwrap();
        let dst = submitter.allocate(256, MemorySpace::Device).unwrap();
        unsafe { submitter.copy_async(&stream, &dst, 0, &src, 0, 256) }.unwrap()
    });
    barrier.wait();
    // The SDK call is in progress but Pending has not reached the reclaimer.
    assert!(!runtime.close(Duration::ZERO));
    barrier.wait();
    let receipt = job.join().unwrap();
    assert!(!runtime.close(Duration::ZERO));
    backend.ready.store(true, Ordering::SeqCst);
    assert!(runtime.close(Duration::from_secs(2)));
    assert!(receipt.query().unwrap());
    assert_eq!(backend.released.load(Ordering::SeqCst), 2);
}
