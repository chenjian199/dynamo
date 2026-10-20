// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Canonical byte broadcasts over the selected platform's collective library.

use anyhow::{Result, ensure};
use dynamo_platform::device::MemorySpace;
use dynamo_platform::memory::DeviceRuntime;
use dynamo_platform_runtime::collectives::NativeCommunicator;
use kvbm_kernels::native::{BlockLayout, NativeCopies};
use kvbm_physical::layout::Layout;
use kvbm_physical::transfer::TransferCompleteNotification;
use parking_lot::Mutex;
use std::ops::Range;
use std::sync::Arc;
use std::time::Duration;
use velo::EventManager;

use super::CollectiveOps;
use crate::BlockId;
use crate::worker::PhysicalWorker;
use kvbm_common::LogicalLayoutHandle;

pub struct NativeCollectives {
    worker: Arc<PhysicalWorker>,
    communicator: Arc<Mutex<NativeCommunicator>>,
    device: DeviceRuntime,
    rank: usize,
    world_size: usize,
    events: Arc<EventManager>,
    runtime: tokio::runtime::Handle,
    pending: Arc<tokio::sync::Semaphore>,
}

impl NativeCollectives {
    pub fn new(
        worker: Arc<PhysicalWorker>,
        communicator: NativeCommunicator,
        device: DeviceRuntime,
        events: Arc<EventManager>,
        runtime: tokio::runtime::Handle,
    ) -> Result<Self> {
        ensure!(
            communicator.identity() == device.identity(),
            "collective/device context mismatch"
        );
        ensure!(
            worker.rank() == Some(communicator.rank() as usize),
            "collective/worker rank mismatch"
        );
        ensure!(
            worker
                .resolve_layout(LogicalLayoutHandle::G1)?
                .native_layout()?
                .identity()
                == device.identity(),
            "collective/layout context mismatch"
        );
        Ok(Self {
            worker,
            rank: communicator.rank() as usize,
            world_size: communicator.world_size() as usize,
            communicator: Arc::new(Mutex::new(communicator)),
            device,
            events,
            runtime,
            pending: Arc::new(tokio::sync::Semaphore::new(64)),
        })
    }
}

impl CollectiveOps for NativeCollectives {
    fn rank(&self) -> usize {
        self.rank
    }
    fn world_size(&self) -> usize {
        self.world_size
    }
    fn broadcast(
        &self,
        src: LogicalLayoutHandle,
        dst: LogicalLayoutHandle,
        source: &[BlockId],
        destination: &[BlockId],
        layer_range: Option<Range<usize>>,
    ) -> Result<TransferCompleteNotification> {
        ensure!(
            src == LogicalLayoutHandle::G1 && dst == LogicalLayoutHandle::G1,
            "collective broadcast requires device layouts"
        );
        ensure!(
            source.len() == destination.len(),
            "broadcast block counts differ"
        );
        let layout = self.worker.resolve_layout(dst)?.native_layout()?.clone();
        let config = layout.config();
        let layers = layer_range.unwrap_or(0..config.num_layers);
        ensure!(
            layers.start < layers.end && layers.end <= config.num_layers,
            "invalid broadcast layer range"
        );
        let mut unique = std::collections::HashSet::new();
        for (&s, &d) in source.iter().zip(destination) {
            ensure!(
                s < config.num_blocks && d < config.num_blocks && unique.insert(d),
                "invalid broadcast block IDs"
            );
        }
        if source.is_empty() {
            return Ok(TransferCompleteNotification::completed());
        }
        let source = source.to_vec();
        let destination = destination.to_vec();
        let permit = self
            .pending
            .clone()
            .try_acquire_owned()
            .map_err(|_| anyhow::anyhow!("collective queue is full"))?;
        let comm = self.communicator.clone();
        let runtime = self.device.clone();
        let event = self.events.new_event()?;
        let awaiter = self.events.awaiter(event.handle())?;
        self.runtime.spawn_blocking(move || {
            let _permit = permit;
            let result = (|| -> Result<()> {
                let mut communicator = comm.lock();
                let mut copies = NativeCopies::new(runtime.clone())?;
                let bytes = config_bytes(layout.as_ref());
                let packed = copies.staging(bytes)?;
                let previous = copies.staging(bytes)?;
                let device = runtime.allocate(bytes, MemorySpace::Device)?;
                let plan = BlockLayout::new(&[1, bytes], &[bytes, 1], 0, 1)?;
                unsafe { packed.write(0, &vec![0; bytes])? };
                for (&s, &d) in source.iter().zip(&destination) {
                    let transfer = (|| -> Result<()> {
                        if communicator.rank() == 0 {
                            unsafe { layout.packed_copy(&mut copies, s, &packed, true)? };
                        }
                        unsafe { copies.packed_copy(&device, &plan, 0, &packed, 0, false)? };
                        unsafe { communicator.broadcast(&device, bytes, Duration::from_secs(60))? };
                        unsafe { copies.packed_copy(&device, &plan, 0, &packed, 0, true)? };
                        if layers != (0..layout.num_layers()) {
                            unsafe { layout.packed_copy(&mut copies, d, &previous, true)? };
                            let layer_bytes = bytes / layout.num_layers();
                            unsafe {
                                std::ptr::copy_nonoverlapping(
                                    (packed.address() + layers.start * layer_bytes) as *const u8,
                                    (previous.address() + layers.start * layer_bytes) as *mut u8,
                                    layers.len() * layer_bytes,
                                )
                            };
                            unsafe { layout.packed_copy(&mut copies, d, &previous, false)? };
                        } else {
                            unsafe { layout.packed_copy(&mut copies, d, &packed, false)? };
                        }
                        Ok(())
                    })();
                    if transfer.is_err() && !copies.drain(Duration::from_secs(30)) {
                        std::process::abort();
                    }
                    transfer?;
                }
                Ok(())
            })();
            match result {
                Ok(()) => {
                    let _ = event.trigger();
                }
                Err(error) => {
                    let _ = event.poison(error.to_string());
                }
            }
        });
        Ok(TransferCompleteNotification::from_awaiter(awaiter))
    }
}

fn config_bytes(layout: &impl Layout) -> usize {
    layout.config().bytes_per_block()
}
