// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Native SDK host-staging implementation for the existing KVBM worker protocol.

use super::utils::{BlockTransferPool, BlockTransferRequest};
use crate::block_manager::storage::torch::{TorchDevice, TorchTensor};
use anyhow::{Result, bail, ensure};
use dynamo_platform::device::MemorySpace;
use dynamo_platform::memory::{Allocation, DeviceRuntime};
use dynamo_platform_runtime::{
    NativeBackend,
    kv::{BlockLayout, BlockTransfers},
};
use std::{fs::File, os::unix::fs::FileExt, sync::Arc, time::Duration};

pub struct PlatformStore {
    copies: BlockTransfers,
    tensors: Vec<(Allocation, BlockLayout)>,
    pub bytes_per_block: usize,
    pub device_blocks: usize,
    host: Option<Allocation>,
    disk: Option<File>,
    staging: Option<Allocation>,
    host_blocks: usize,
    disk_blocks: usize,
    initialized: bool,
    poisoned: bool,
    verify: bool,
}

impl PlatformStore {
    /// Tensor pointers and their owners are supplied only by the trusted engine
    /// binding. Caller must prevent concurrent writes while a block is offloaded.
    ///
    /// # Safety
    /// Every descriptor must refer to a live dense tensor in the selected SDK
    /// context; its owner must retain that storage for the lifetime of this store.
    pub unsafe fn new(
        platform: &str,
        device: usize,
        tensors: &[Arc<dyn TorchTensor>],
        block_axes: &[usize],
        blocks: usize,
        element_bytes: usize,
    ) -> Result<Self> {
        ensure!(
            !tensors.is_empty() && tensors.len() == block_axes.len(),
            "missing tensor layouts"
        );
        let runtime = DeviceRuntime::new(NativeBackend::open(platform, device.try_into()?)?, 64)?;
        let mut imported = Vec::new();
        let mut bytes_per_block = 0usize;
        for (tensor, &axis) in tensors.iter().zip(block_axes) {
            ensure!(
                tensor.device() == TorchDevice::Platform(platform.into(), device),
                "tensor platform/device mismatch"
            );
            let layout = BlockLayout::new(&tensor.shape(), &tensor.stride(), axis, element_bytes)?;
            ensure!(
                layout.blocks() == blocks && layout.allocation_bytes() == tensor.size_bytes(),
                "tensor size/block count mismatch"
            );
            bytes_per_block = bytes_per_block
                .checked_add(layout.bytes_per_block())
                .ok_or_else(|| anyhow::anyhow!("KV block byte size overflow"))?;
            // Arc<Arc<dyn TorchTensor>> gives the Any owner a concrete type while
            // retaining the original Python tensor until the final DMA completes.
            let allocation = unsafe {
                runtime.borrow(
                    tensor.data_ptr().try_into()?,
                    tensor.size_bytes(),
                    MemorySpace::Device,
                    Arc::new(tensor.clone()),
                    false,
                )?
            };
            imported.push((allocation, layout));
        }
        Ok(Self {
            copies: BlockTransfers::new(runtime)?,
            tensors: imported,
            bytes_per_block,
            device_blocks: blocks,
            host: None,
            disk: None,
            staging: None,
            host_blocks: 0,
            disk_blocks: 0,
            initialized: false,
            poisoned: false,
            verify: std::env::var("DYN_KVBM_VERIFY_TRANSFERS").as_deref() == Ok("1"),
        })
    }

    pub fn allocate(&mut self, host_blocks: usize, disk_blocks: usize) -> Result<()> {
        ensure!(!self.initialized, "platform store already allocated");
        let size = |n: usize| {
            self.bytes_per_block
                .checked_mul(n)
                .filter(|&b| b <= isize::MAX as usize)
                .ok_or_else(|| anyhow::anyhow!("offload capacity overflow"))
        };
        let host = if host_blocks > 0 {
            Some(self.copies.allocate_host(size(host_blocks)?)?)
        } else {
            None
        };
        let (disk, staging) = if disk_blocks > 0 {
            let path = std::env::var("DYN_KVBM_DISK_CACHE_DIR")?;
            // Buffered local I/O is an explicit fallback: no GDS or O_DIRECT claim.
            let file = tempfile::tempfile_in(path)?;
            file.set_len(size(disk_blocks)?.try_into()?)?;
            (
                Some(file),
                Some(self.copies.allocate_host(self.bytes_per_block)?),
            )
        } else {
            (None, None)
        };
        self.host = host;
        self.disk = disk;
        self.staging = staging;
        self.host_blocks = host_blocks;
        self.disk_blocks = disk_blocks;
        self.initialized = true;
        Ok(())
    }

    pub fn execute(&mut self, request: &BlockTransferRequest) -> Result<()> {
        use BlockTransferPool::*;
        ensure!(
            self.initialized && !self.poisoned,
            "platform store not ready or poisoned"
        );
        ensure!(
            matches!(
                (request.from_pool, request.to_pool),
                (Device, Host) | (Host, Device) | (Device, Disk) | (Disk, Device) | (Host, Disk)
            ),
            "unsupported KVBM transfer direction"
        );
        let capacity = |pool| match pool {
            Device => self.device_blocks,
            Host => self.host_blocks,
            Disk => self.disk_blocks,
        };
        let mut destinations = std::collections::HashSet::new();
        for &(from, to) in &request.blocks {
            ensure!(
                from < capacity(request.from_pool) && to < capacity(request.to_pool),
                "block index out of bounds"
            );
            ensure!(destinations.insert(to), "duplicate destination block");
        }
        for &(from, to) in &request.blocks {
            if let Err(error) = self.copy_one(request.from_pool, request.to_pool, from, to) {
                self.poisoned = true;
                // An uncertain DMA must never permit the scheduler to reuse a
                // live engine block. Successful drain makes a normal error safe.
                if !self.copies.drain(Duration::from_secs(30)) {
                    tracing::error!(error = %error, "KVBM DMA could not drain; terminating worker to retain block safety");
                    std::process::abort();
                }
                return Err(error);
            }
        }
        tracing::debug!(source = ?request.from_pool, target = ?request.to_pool,
            blocks = request.blocks.len(), bytes_per_block = self.bytes_per_block,
            "platform KVBM transfer completed");
        Ok(())
    }

    fn copy_one(
        &mut self,
        source: BlockTransferPool,
        target: BlockTransferPool,
        from: usize,
        to: usize,
    ) -> Result<()> {
        use BlockTransferPool::*;
        let host = if source == Disk || target == Disk {
            self.staging
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("disk staging missing"))?
        } else {
            self.host
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("host pool missing"))?
        };
        let host_offset = match (source, target) {
            (Device, Host) => to * self.bytes_per_block,
            (Host, Device) => from * self.bytes_per_block,
            _ => 0,
        };
        if source == Disk {
            let mut bytes = vec![0u8; self.bytes_per_block];
            self.disk
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("disk pool missing"))?
                .read_exact_at(&mut bytes, (from * self.bytes_per_block) as u64)?;
            unsafe {
                host.write(0, &bytes)?;
            }
        }
        if source == Device || target == Device {
            let block = if source == Device { from } else { to };
            let mut offset = host_offset;
            for (tensor, layout) in &self.tensors {
                unsafe {
                    self.copies.copy_block(
                        tensor,
                        layout,
                        block,
                        host,
                        offset,
                        source == Device,
                    )?;
                }
                offset += layout.bytes_per_block();
            }
            if self.verify && target == Device {
                // Acceptance/debug mode: independently read back the restored
                // engine block before publishing completion to its scheduler.
                let check = self.copies.allocate_host(self.bytes_per_block)?;
                let mut offset = 0;
                for (tensor, layout) in &self.tensors {
                    unsafe {
                        self.copies
                            .copy_block(tensor, layout, block, &check, offset, true)?;
                    }
                    offset += layout.bytes_per_block();
                }
                let mut expected = vec![0; self.bytes_per_block];
                let mut actual = vec![0; self.bytes_per_block];
                unsafe {
                    host.read(host_offset, &mut expected)?;
                    check.read(0, &mut actual)?;
                }
                ensure!(
                    actual == expected,
                    "KVBM reloaded bytes differ from stored block"
                );
                tracing::debug!(block, "platform KVBM reload byte verification passed");
            }
        }
        if target == Disk {
            let mut bytes = vec![0u8; self.bytes_per_block];
            if source == Host {
                unsafe {
                    self.host
                        .as_ref()
                        .ok_or_else(|| anyhow::anyhow!("host pool missing"))?
                        .read(from * self.bytes_per_block, &mut bytes)?;
                }
            } else if source == Device {
                unsafe {
                    host.read(0, &mut bytes)?;
                }
            } else {
                bail!("invalid disk source");
            }
            self.disk
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("disk pool missing"))?
                .write_all_at(&bytes, (to * self.bytes_per_block) as u64)?;
        }
        Ok(())
    }
}
