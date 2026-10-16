// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Run in the corresponding vendor container: device_smoke metax|ascend|cuda.
use dynamo_memory::platform::PlatformStorage;
use dynamo_memory::{ArenaAllocator, Buffer, MemoryDescriptor, OffsetBuffer, SystemStorage};
use dynamo_platform::device::MemorySpace::{Device, Host};
use dynamo_platform::memory::DeviceRuntime;
use dynamo_platform_runtime::NativeBackend;
use std::sync::Arc;
use std::time::Duration;

// Verify restoration of a real pre-existing engine-style context on this thread.
fn check_context_restore(
    platform: &str,
    runtime: &DeviceRuntime,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::ffi::c_void;
    let (library, get, set) = match platform {
        "cuda" => ("libcuda.so.1", "cuCtxGetCurrent", "cuCtxSetCurrent"),
        "metax" => ("libmcruntime.so", "mcCtxGetCurrent", "mcCtxSetCurrent"),
        _ => (
            "libascendcl.so",
            "aclrtGetCurrentContext",
            "aclrtSetCurrentContext",
        ),
    };
    unsafe {
        let sdk = libloading::Library::new(library)?;
        let get: libloading::Symbol<unsafe extern "C" fn(*mut *mut c_void) -> i32> =
            sdk.get(get.as_bytes())?;
        let set: libloading::Symbol<unsafe extern "C" fn(*mut c_void) -> i32> =
            sdk.get(set.as_bytes())?;
        let mut previous = std::ptr::null_mut();
        assert_eq!(get(&mut previous), 0);
        let expected = runtime.identity().context as *mut c_void;
        assert_eq!(set(expected), 0);
        let other = DeviceRuntime::new(NativeBackend::open(platform, 1)?, 1)?;
        drop(other.allocate(4096, Device)?);
        let mut actual = std::ptr::null_mut();
        assert_eq!(get(&mut actual), 0);
        assert_eq!(actual, expected);
        if !previous.is_null() || platform != "ascend" {
            assert_eq!(set(previous), 0);
        }
    }
    println!("existing caller context restoration: pass");
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let platform = std::env::args().nth(1).ok_or("expected platform")?;
    let backend = NativeBackend::open(&platform, 0)?;
    let runtime = DeviceRuntime::new(backend.clone(), 32)?;
    println!(
        "identity={:?} count={}",
        runtime.identity(),
        runtime.device_count()
    );
    assert!(NativeBackend::open(&platform, runtime.device_count()).is_err());
    assert!(runtime.allocate(0, Device).is_err());
    assert!(runtime.allocate(usize::MAX, Device).is_err());
    let bytes = 1024 * 1024;
    let data: Vec<u8> = (0..bytes).map(|i| (i % 251) as u8).collect();
    let mut warm_free = 0;
    for iteration in 0..64 {
        let input = runtime.allocate(bytes, Host)?;
        let output = runtime.allocate(bytes, Host)?;
        let a = runtime.allocate(bytes, Device)?;
        let b = runtime.allocate(bytes, Device)?;
        let stream = runtime.create_stream()?;
        unsafe {
            input.write(0, &data)?;
            let first = runtime.copy_async(&stream, &a, 0, &input, 0, bytes)?;
            let second = runtime.copy_async(&stream, &b, 0, &a, 0, bytes)?;
            let last = runtime.copy_async(&stream, &output, 0, &b, 0, bytes)?;
            // Cancel observation and release caller owners while DMA is in flight.
            drop(first);
            drop(second);
            drop(input);
            drop(a);
            assert!(last.wait(Duration::from_secs(30))?);
            let mut actual = vec![0; bytes];
            output.read(0, &mut actual)?;
            assert_eq!(actual, data);
            assert!(
                runtime
                    .copy_async(&stream, &b, bytes, &output, 0, 1)
                    .is_err()
            );
            assert!(runtime.copy_async(&stream, &b, 0, &b, 0, 64).is_err());
            if platform == "ascend" {
                let aligned = runtime.allocate(bytes, Device)?;
                assert!(runtime.copy_async(&stream, &aligned, 1, &b, 1, 64).is_err());
            }
        }
        let storage = PlatformStorage::new(b);
        let buffer = Buffer::new(storage.clone());
        let view = OffsetBuffer::new(buffer, 64, 128)?;
        assert_eq!(view.device_identity(), Some(runtime.identity()));
        assert_eq!(
            view.cuda_device_index(),
            if platform == "cuda" { Some(0) } else { None }
        );
        let arena = ArenaAllocator::new(storage, 4096)?;
        let chunk = arena.allocate(4096)?;
        assert_eq!(chunk.device_identity(), Some(runtime.identity()));
        assert!(runtime.drain(Duration::from_secs(30)));
        drop(chunk);
        drop(arena);
        drop(view);
        drop(output);
        drop(stream);
        let (free, _) = backend.memory_info()?;
        if iteration == 0 {
            warm_free = free;
        }
        if iteration == 63 {
            println!("device free bytes after warmup={warm_free}, after 64 cycles={free}");
            assert!(
                free.saturating_add(8 * 1024 * 1024) >= warm_free,
                "device memory grew beyond 8 MiB tolerance"
            );
        }
        if iteration == 0 {
            println!(
                "H2D/D2D/D2H byte parity, cancellation, bounds and buffer/arena identity: pass"
            );
        }
    }
    let owned_device = Arc::new(runtime.allocate(4096, Device)?);
    let weak_device = Arc::downgrade(&owned_device);
    let borrowed_device = unsafe {
        runtime.borrow(
            owned_device.address(),
            4096,
            Device,
            owned_device.clone(),
            false,
        )?
    };
    let owned_stream = Arc::new(runtime.create_stream()?);
    let weak_stream = Arc::downgrade(&owned_stream);
    let borrowed_stream =
        unsafe { runtime.borrow_stream(owned_stream.raw_handle(), owned_stream.clone()) };
    let host = runtime.allocate(4096, Host)?;
    unsafe {
        host.write(0, &[11; 4096])?;
    }
    drop(owned_device);
    drop(owned_stream);
    let receipt =
        unsafe { runtime.copy_async(&borrowed_stream, &borrowed_device, 0, &host, 0, 4096)? };
    drop(borrowed_device);
    drop(borrowed_stream);
    drop(host);
    drop(receipt);
    assert!(runtime.drain(Duration::from_secs(30)));
    assert!(weak_device.upgrade().is_none());
    assert!(weak_stream.upgrade().is_none());
    println!("borrowed native device/stream owners: pass");
    // Transfer ownership across host threads and exercise context rebinding.
    let other = runtime.clone();
    std::thread::spawn(move || {
        let a = other.allocate(4096, Device).unwrap();
        let h = other.allocate(4096, Host).unwrap();
        let stream = other.create_stream().unwrap();
        unsafe {
            h.write(0, &[9; 4096]).unwrap();
            assert!(
                other
                    .copy_async(&stream, &a, 0, &h, 0, 4096)
                    .unwrap()
                    .wait(Duration::from_secs(30))
                    .unwrap()
            );
        }
    })
    .join()
    .map_err(|_| "thread failed")?;
    println!("cross-thread context: pass");
    for _ in 0..64 {
        let owner = Arc::new(SystemStorage::new(4096)?);
        let weak = Arc::downgrade(&owner);
        let borrowed =
            unsafe { runtime.borrow(owner.addr(), owner.size(), Host, owner.clone(), true) };
        match borrowed {
            Ok(host) => {
                drop(owner);
                assert!(weak.upgrade().is_some());
                let a = runtime.allocate(4096, Device)?;
                let s = runtime.create_stream()?;
                let receipt = unsafe { runtime.copy_async(&s, &a, 0, &host, 0, 4096)? };
                drop(host);
                drop(a);
                drop(s);
                drop(receipt);
                assert!(runtime.drain(Duration::from_secs(30)));
                assert!(weak.upgrade().is_none());
            }
            Err(e) => return Err(e.into()),
        }
    }
    println!("64 host registration/owner release cycles: pass");
    if runtime.device_count() > 1 {
        check_context_restore(&platform, &runtime)?;
        let other = DeviceRuntime::new(NativeBackend::open(&platform, 1)?, 4)?;
        let a = other.allocate(4096, Device)?;
        let h = runtime.allocate(4096, Host)?;
        let s = runtime.create_stream()?;
        assert!(unsafe { runtime.copy_async(&s, &a, 0, &h, 0, 4096) }.is_err());
        println!("wrong-device rejection: pass");
    }
    assert!(runtime.drain(Duration::ZERO));
    assert!(runtime.drain(Duration::ZERO));
    assert!(runtime.close(Duration::ZERO));
    assert!(runtime.close(Duration::ZERO));
    println!("64 allocation/copy/release cycles, repeated drain/close: pass");
    Ok(())
}
