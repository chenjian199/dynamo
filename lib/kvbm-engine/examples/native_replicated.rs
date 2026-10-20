// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Hardware acceptance for the independent engine/physical/kernel path.
//! Run each rank as a separate process with a shared fresh bootstrap directory.

use anyhow::{Context, Result, ensure};
use dynamo_memory::{MemoryDescriptor, platform::PlatformStorage};
use dynamo_platform::device::MemorySpace;
use dynamo_platform::memory::DeviceRuntime;
use dynamo_platform_runtime::{NativeBackend, collectives::NativeCommunicator};
use kvbm_common::LogicalLayoutHandle::{G1, G2, G3};
use kvbm_engine::collectives::NativeCollectives;
use kvbm_engine::worker::{PhysicalWorker, ReplicatedDataWorker, WorkerTransfers};
use kvbm_kernels::native::NativeCopies;
use kvbm_physical::layout::{BlockDimension, KvBlockLayout, Layout, LayoutConfig, PhysicalLayout};
use kvbm_physical::native::{NativeLayout, NativeTransfers};
use kvbm_physical::{TransferManager, TransferOptions};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};
use velo::EventManager;

fn write(
    layout: &NativeLayout,
    copies: &mut NativeCopies,
    block: usize,
    data: &[u8],
) -> Result<()> {
    let packed = copies.staging(data.len())?;
    unsafe {
        packed.write(0, data)?;
        layout.packed_copy(copies, block, &packed, false)?;
    }
    Ok(())
}
fn read(layout: &NativeLayout, copies: &mut NativeCopies, block: usize) -> Result<Vec<u8>> {
    let bytes = layout.config().bytes_per_block();
    let packed = copies.staging(bytes)?;
    let mut data = vec![0; bytes];
    unsafe {
        layout.packed_copy(copies, block, &packed, true)?;
        packed.read(0, &mut data)?;
    }
    Ok(data)
}

// Independent reference indexing: no BlockLayout or pack/unpack plan is used.
fn raw_parity(
    layout: &NativeLayout,
    runtime: &DeviceRuntime,
    block: usize,
    rank: u32,
    expected: &[u8],
) -> Result<()> {
    let c = layout.config();
    let heads = c.num_heads.context("missing head count")?;
    let head_dim = c.inner_dim / heads;
    let stream = runtime.create_stream()?;
    for (layer, region) in layout.memory_regions().iter().enumerate() {
        let allocation = region
            .as_any()
            .downcast_ref::<PlatformStorage>()
            .context("not native storage")?
            .allocation();
        let host = runtime.allocate(allocation.len(), MemorySpace::Host)?;
        let completion =
            unsafe { runtime.copy_async(&stream, &host, 0, allocation, 0, allocation.len())? };
        ensure!(
            completion.wait(Duration::from_secs(30))?,
            "raw readback timed out"
        );
        let mut raw = vec![0; host.len()];
        unsafe { host.read(0, &mut raw)? };
        for outer in 0..c.outer_dim {
            for token in 0..c.page_size {
                for head in 0..heads {
                    for d in 0..head_dim {
                        for byte in 0..c.dtype_width_bytes {
                            let logical = ((((layer * c.outer_dim + outer) * c.page_size + token)
                                * heads
                                + head)
                                * head_dim
                                + d)
                                * c.dtype_width_bytes
                                + byte;
                            let slot = if rank == 0 {
                                block * c.outer_dim + outer
                            } else {
                                outer * c.num_blocks + block
                            };
                            let inner = if rank == 0 {
                                (token * heads + head) * head_dim + d
                            } else {
                                (head * c.page_size + token) * head_dim + d
                            };
                            let physical = (slot * c.page_size * c.inner_dim + inner)
                                * c.dtype_width_bytes
                                + byte;
                            ensure!(
                                raw[physical] == expected[logical],
                                "rank {rank} raw byte mismatch at layer {layer}, logical {logical}"
                            );
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    ensure!(
        args.len() == 8,
        "usage: native_replicated PLATFORM DEVICE RANK WORLD DIRECTORY OUTER WIDTH"
    );
    let platform = &args[1];
    let device: u32 = args[2].parse()?;
    let rank: u32 = args[3].parse()?;
    let world: u32 = args[4].parse()?;
    let directory = PathBuf::from(&args[5]);
    let outer: usize = args[6].parse()?;
    let width: usize = args[7].parse()?;
    let backend = NativeBackend::open(platform, device)?;
    let runtime = DeviceRuntime::new(backend.clone(), 32)?;
    let root_path = directory.join("root.bin");
    if rank == 0 {
        let root = NativeCommunicator::root_info(&backend)?;
        let tmp = directory.join("root.tmp");
        std::fs::write(&tmp, &root)?;
        std::fs::rename(tmp, &root_path)?;
    }
    let deadline = Instant::now() + Duration::from_secs(60);
    while !root_path.exists() {
        ensure!(
            Instant::now() < deadline,
            "bootstrap file deadline exceeded"
        );
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let root = std::fs::read(root_path)?;
    let communicator = NativeCommunicator::new(backend, &root, rank, world)?;
    let events = Arc::new(EventManager::local());
    let handle = tokio::runtime::Handle::current();
    let manager = TransferManager::from_native(NativeTransfers::new(
        runtime.clone(),
        events.clone(),
        handle.clone(),
    )?);
    let config = LayoutConfig::builder()
        .num_blocks(4)
        .num_layers(3)
        .outer_dim(outer)
        .page_size(4)
        .inner_dim(16)
        .num_heads(Some(2))
        .dtype_width_bytes(width)
        .build()?;
    let allocated = NativeLayout::allocate_layers(
        &runtime,
        config.clone(),
        if rank == 0 {
            KvBlockLayout::OperationalNHD
        } else {
            KvBlockLayout::OperationalHND
        },
        if rank == 0 {
            BlockDimension::BlockIsFirstDim
        } else {
            BlockDimension::BlockIsSecondDim
        },
        MemorySpace::Device,
    )?;
    let allocations = allocated
        .memory_regions()
        .iter()
        .map(|region| {
            region
                .as_any()
                .downcast_ref::<PlatformStorage>()
                .context("missing allocation owner")
                .map(|s| s.allocation().clone())
        })
        .collect::<Result<Vec<_>>>()?;
    let g1 = PhysicalLayout::from_native(NativeLayout::from_allocations(
        config.clone(),
        allocated.block_layout(),
        Some(if rank == 0 {
            BlockDimension::BlockIsFirstDim
        } else {
            BlockDimension::BlockIsSecondDim
        }),
        allocations.clone(),
    )?);
    let alias = PhysicalLayout::from_native(NativeLayout::from_allocations(
        config.clone(),
        allocated.block_layout(),
        Some(if rank == 0 {
            BlockDimension::BlockIsFirstDim
        } else {
            BlockDimension::BlockIsSecondDim
        }),
        allocations,
    )?);
    drop(allocated);
    let g1_data = g1.native_layout()?.clone();
    let g1_handle = manager.register_layout(g1)?;
    let alias_handle = manager.register_layout(alias)?;
    let mut builder = PhysicalWorker::builder()
        .manager(manager.clone())
        .g1_handle(g1_handle)
        .rank(rank as usize);
    if rank == 0 {
        let g2 = manager.register_layout(PhysicalLayout::from_native(NativeLayout::allocate(
            &runtime,
            config.clone(),
            KvBlockLayout::UniversalTP,
            MemorySpace::Host,
        )?))?;
        let g3 = manager.register_layout(PhysicalLayout::from_native(NativeLayout::disk(
            &runtime,
            config.clone(),
            KvBlockLayout::UniversalPP,
            &directory,
        )?))?;
        builder = builder.g2_handle(g2).g3_handle(g3);
    }
    let worker = Arc::new(builder.build()?);
    let collective = Arc::new(NativeCollectives::new(
        worker.clone(),
        communicator,
        runtime.clone(),
        events.clone(),
        handle.clone(),
    )?);
    let replicated = ReplicatedDataWorker::with_events(worker, events, handle, collective)?;
    let mut copies = NativeCopies::new(runtime.clone())?;
    let bytes = config.bytes_per_block();
    let expected: Vec<u8> = (0..bytes)
        .map(|i| ((i * 37 + i / 11 + 17) % 251) as u8)
        .collect();
    let zero = vec![0; bytes];
    for block in 0..4 {
        write(&g1_data, &mut copies, block, &zero)?;
    }
    write(
        &g1_data,
        &mut copies,
        0,
        if rank == 0 { &expected } else { &zero },
    )?;
    for tier in [G2, G3] {
        replicated
            .execute_local_transfer(
                G1,
                tier,
                Arc::from([0]),
                Arc::from([1]),
                TransferOptions::default(),
            )?
            .await?;
        write(&g1_data, &mut copies, 0, &zero)?;
        replicated
            .execute_local_transfer(
                tier,
                G1,
                Arc::from([1]),
                Arc::from([0]),
                TransferOptions::default(),
            )?
            .await?;
        ensure!(
            read(&g1_data, &mut copies, 0)? == expected,
            "rank {rank}: {tier:?} broadcast parity failed"
        );
        raw_parity(&g1_data, &runtime, 0, rank, &expected)?;
    }
    replicated
        .execute_local_transfer(
            G2,
            G3,
            Arc::from([1]),
            Arc::from([2]),
            TransferOptions::default(),
        )?
        .await?;
    replicated
        .execute_local_transfer(
            G3,
            G2,
            Arc::from([2]),
            Arc::from([3]),
            TransferOptions::default(),
        )?
        .await?;
    replicated
        .execute_local_transfer(
            G2,
            G1,
            Arc::from([3]),
            Arc::from([0]),
            TransferOptions::default(),
        )?
        .await?;
    raw_parity(&g1_data, &runtime, 0, rank, &expected)?;
    replicated
        .execute_local_transfer(
            G1,
            G1,
            Arc::from([0]),
            Arc::from([1]),
            TransferOptions::default(),
        )?
        .await?;
    raw_parity(&g1_data, &runtime, 1, rank, &expected)?;
    // Every rank must preserve the other layers, even though only root owns G2.
    write(&g1_data, &mut copies, 2, &zero)?;
    replicated
        .execute_local_transfer(
            G2,
            G1,
            Arc::from([1]),
            Arc::from([2]),
            TransferOptions::from_layer_range(Some(1..2)),
        )?
        .await?;
    let mut partial = zero.clone();
    let per_layer = bytes / config.num_layers;
    partial[per_layer..2 * per_layer].copy_from_slice(&expected[per_layer..2 * per_layer]);
    ensure!(
        read(&g1_data, &mut copies, 2)? == partial,
        "partial layer preservation failed"
    );
    raw_parity(&g1_data, &runtime, 2, rank, &partial)?;
    // Dropping an awaiter must not cancel transfer ownership or collective order.
    let cancelled = replicated.execute_local_transfer(
        G2,
        G1,
        Arc::from([1]),
        Arc::from([3]),
        TransferOptions::default(),
    )?;
    drop(cancelled);
    replicated
        .execute_local_transfer(
            G2,
            G1,
            Arc::from([1]),
            Arc::from([0]),
            TransferOptions::default(),
        )?
        .await?;
    ensure!(
        read(&g1_data, &mut copies, 3)? == expected,
        "cancelled waiter released work early"
    );
    ensure!(
        manager.export_metadata().is_err(),
        "local native layout exported without remote registration"
    );
    ensure!(
        manager
            .execute_transfer(
                g1_handle,
                &[0],
                alias_handle,
                &[1],
                TransferOptions::default()
            )
            .is_err(),
        "aliased layouts accepted"
    );
    ensure!(
        replicated
            .execute_local_transfer(
                kvbm_common::LogicalLayoutHandle::G4,
                G2,
                Arc::from([0]),
                Arc::from([0]),
                TransferOptions::default()
            )
            .is_err(),
        "unsupported G4 reported success"
    );
    ensure!(
        manager
            .execute_transfer(
                g1_handle,
                &[0],
                g1_handle,
                &[config.num_blocks],
                TransferOptions::default()
            )
            .is_err(),
        "invalid block ID accepted"
    );
    ensure!(
        manager
            .execute_transfer(
                g1_handle,
                &[0, 1],
                g1_handle,
                &[2, 2],
                TransferOptions::default()
            )
            .is_err(),
        "duplicate destination accepted"
    );
    manager
        .execute_transfer(g1_handle, &[], g1_handle, &[], TransferOptions::default())?
        .await?;
    ensure!(
        runtime.drain(Duration::from_secs(30)),
        "copy queue did not drain"
    );
    println!(
        "platform={platform} rank={rank} world={world} outer={outer} width={width} passed=true g2=true g3=true host_disk=true layout_parity=true raw_reference=true local_device_copy=true partial_layers=true dropped_waiter=true remote_rejected=true alias_rejected=true g4_rejected=true"
    );
    std::fs::write(directory.join(format!("rank-{rank}.done")), b"passed")?;
    let deadline = Instant::now() + Duration::from_secs(30);
    while !(0..world).all(|i| directory.join(format!("rank-{i}.done")).exists()) {
        ensure!(Instant::now() < deadline, "peer completion timeout");
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    Ok(())
}
