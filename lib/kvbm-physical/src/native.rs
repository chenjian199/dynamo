// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Local SDK-backed layouts and transfers. Remote registration is deliberately absent.

use anyhow::{Result, anyhow, ensure};
use dynamo_memory::{Buffer, MemoryRegion, StorageKind, platform::PlatformStorage};
use dynamo_platform::device::{DeviceIdentity, MemorySpace};
use dynamo_platform::memory::{Allocation, DeviceRuntime};
use kvbm_kernels::native::{BlockLayout, NativeCopies};
use parking_lot::Mutex;
use std::fs::File;
use std::ops::Range;
use std::os::unix::fs::FileExt;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;
use validator::Validate;
use velo::EventManager;

use crate::layout::{
    BlockDim, BlockDimension, BlockFormat, FullyContiguousDetails, KvBlockLayout,
    LayerSeparateDetails, Layout, LayoutConfig, LayoutTypeDetails,
};
use crate::transfer::{TransferCompleteNotification, TransferOptions};
use crate::{BlockId, layout::PhysicalLayout};

#[derive(Debug)]
enum Backing {
    Memory(Allocation),
    File(File),
}

/// A validated dense tensor and its owner. Canonical packed order is L,O,T,H,D.
#[derive(Debug)]
pub struct NativeLayout {
    config: LayoutConfig,
    format: KvBlockLayout,
    identity: DeviceIdentity,
    plan: BlockLayout,
    backing: Vec<Backing>,
    layer_separate: Option<BlockDimension>,
    regions: Vec<Buffer>,
}

impl NativeLayout {
    pub fn allocate(
        runtime: &DeviceRuntime,
        config: LayoutConfig,
        format: KvBlockLayout,
        space: MemorySpace,
    ) -> Result<Self> {
        Self::allocate_kind(runtime, config, format, space, None)
    }

    pub fn allocate_layers(
        runtime: &DeviceRuntime,
        config: LayoutConfig,
        format: KvBlockLayout,
        block_dimension: BlockDimension,
        space: MemorySpace,
    ) -> Result<Self> {
        ensure!(
            matches!(
                format,
                KvBlockLayout::OperationalNHD | KvBlockLayout::OperationalHND
            ),
            "layer-separated tensors require NHD/HND"
        );
        Self::allocate_kind(runtime, config, format, space, Some(block_dimension))
    }

    fn allocate_kind(
        runtime: &DeviceRuntime,
        config: LayoutConfig,
        format: KvBlockLayout,
        space: MemorySpace,
        layer_separate: Option<BlockDimension>,
    ) -> Result<Self> {
        Self::validate_size(&config)?;
        let mut tensor_config = config.clone();
        if layer_separate.is_some() {
            tensor_config.num_layers = 1;
        }
        let plan = Self::plan(
            &tensor_config,
            format,
            layer_separate == Some(BlockDimension::BlockIsSecondDim),
        )?;
        let count = if layer_separate.is_some() {
            config.num_layers
        } else {
            1
        };
        let mut backing = Vec::with_capacity(count);
        let mut regions = Vec::with_capacity(count);
        for _ in 0..count {
            let allocation = runtime.allocate(plan.allocation_bytes(), space)?;
            regions.push(Buffer::from_arc(Arc::new(PlatformStorage::new(
                allocation.clone(),
            ))));
            backing.push(Backing::Memory(allocation));
        }
        let layout = Self {
            config,
            format,
            identity: runtime.identity().clone(),
            plan,
            backing,
            regions,
            layer_separate,
        };
        // Partial layer updates must preserve initialized bytes in other layers.
        let mut copies = NativeCopies::new(runtime.clone())?;
        let zero = copies.staging(layout.config.bytes_per_block())?;
        unsafe {
            zero.write(0, &vec![0; zero.len()])?;
        }
        for block in 0..layout.config.num_blocks {
            let result = unsafe { layout.packed_copy(&mut copies, block, &zero, false) };
            if result.is_err() && !copies.drain(Duration::from_secs(30)) {
                std::process::abort();
            }
            result?;
        }
        Ok(layout)
    }

    /// Register already-owned engine allocations without initializing or changing them.
    /// Allocations retain their M04 owners, including borrowed engine tensor owners.
    /// Callers must synchronize engine writes and hold exclusive block leases for transfers.
    pub fn from_allocations(
        config: LayoutConfig,
        format: KvBlockLayout,
        layer_separate: Option<BlockDimension>,
        allocations: Vec<Allocation>,
    ) -> Result<Self> {
        Self::validate_size(&config)?;
        if layer_separate.is_some() {
            ensure!(
                matches!(
                    format,
                    KvBlockLayout::OperationalNHD | KvBlockLayout::OperationalHND
                ),
                "layer-separated tensors require NHD/HND"
            );
        }
        let mut tensor_config = config.clone();
        if layer_separate.is_some() {
            tensor_config.num_layers = 1;
        }
        let plan = Self::plan(
            &tensor_config,
            format,
            layer_separate == Some(BlockDimension::BlockIsSecondDim),
        )?;
        let count = if layer_separate.is_some() {
            config.num_layers
        } else {
            1
        };
        ensure!(
            allocations.len() == count,
            "allocation count does not match layout"
        );
        let identity = allocations[0].identity().clone();
        let space = allocations[0].space();
        ensure!(
            allocations.iter().all(|a| a.identity() == &identity
                && a.space() == space
                && a.len() == plan.allocation_bytes()),
            "allocation size, device or memory space mismatch"
        );
        let mut ranges: Vec<_> = allocations
            .iter()
            .map(|a| (a.address(), a.address().saturating_add(a.len())))
            .collect();
        ranges.sort_unstable();
        ensure!(
            ranges.windows(2).all(|pair| pair[0].1 <= pair[1].0),
            "layer allocations overlap"
        );
        let regions = allocations
            .iter()
            .map(|a| Buffer::from_arc(Arc::new(PlatformStorage::new(a.clone()))))
            .collect();
        Ok(Self {
            config,
            format,
            identity,
            plan,
            layer_separate,
            regions,
            backing: allocations.into_iter().map(Backing::Memory).collect(),
        })
    }

    pub fn disk(
        runtime: &DeviceRuntime,
        config: LayoutConfig,
        format: KvBlockLayout,
        directory: &Path,
    ) -> Result<Self> {
        let plan = Self::plan(&config, format, false)?;
        let file = tempfile::tempfile_in(directory)?;
        file.set_len(plan.allocation_bytes() as u64)?;
        Ok(Self {
            config,
            format,
            identity: runtime.identity().clone(),
            plan,
            backing: vec![Backing::File(file)],
            regions: vec![],
            layer_separate: None,
        })
    }

    fn validate_size(config: &LayoutConfig) -> Result<()> {
        config.validate()?;
        let bytes = [
            config.num_blocks,
            config.num_layers,
            config.outer_dim,
            config.page_size,
            config.inner_dim,
            config.dtype_width_bytes,
        ]
        .into_iter()
        .try_fold(1usize, usize::checked_mul);
        ensure!(
            bytes.is_some_and(|n| n <= isize::MAX as usize),
            "layout byte size overflow"
        );
        Ok(())
    }

    fn plan(
        config: &LayoutConfig,
        format: KvBlockLayout,
        block_second: bool,
    ) -> Result<BlockLayout> {
        Self::validate_size(config)?;
        let heads = config
            .num_heads
            .ok_or_else(|| anyhow!("native layout requires num_heads"))?;
        ensure!(
            heads > 0 && config.inner_dim.is_multiple_of(heads),
            "invalid head dimensions"
        );
        let order = format
            .dim_order()
            .ok_or_else(|| anyhow!("native layout must be explicit"))?;
        let shape = [
            config.num_blocks,
            config.num_layers,
            config.outer_dim,
            config.page_size,
            heads,
            config.inner_dim / heads,
        ];
        let mut strides = [0; 6];
        strides[5] = 1;
        let mut stride = shape[5];
        for dim in order.iter().rev() {
            let axis = match dim {
                BlockDim::Layer => 1,
                BlockDim::Outer => 2,
                BlockDim::Page => 3,
                BlockDim::Head => 4,
            };
            ensure!(strides[axis] == 0, "duplicate layout dimension");
            strides[axis] = stride;
            stride = stride
                .checked_mul(shape[axis])
                .ok_or_else(|| anyhow!("layout overflow"))?;
        }
        strides[0] = stride;
        if block_second {
            strides[0] = strides[2];
            strides[2] = strides[2]
                .checked_mul(config.num_blocks)
                .ok_or_else(|| anyhow!("layout overflow"))?;
        }
        Ok(BlockLayout::new(
            &shape,
            &strides,
            0,
            config.dtype_width_bytes,
        )?)
    }

    pub fn identity(&self) -> &DeviceIdentity {
        &self.identity
    }
    fn overlaps(&self, other: &Self) -> bool {
        self.backing.iter().any(|a| {
            other.backing.iter().any(|b| match (a, b) {
                (Backing::Memory(a), Backing::Memory(b)) => {
                    a.space() == b.space()
                        && a.address() < b.address().saturating_add(b.len())
                        && b.address() < a.address().saturating_add(a.len())
                }
                _ => false,
            })
        })
    }
    pub fn allocation(&self) -> Result<&Allocation> {
        ensure!(
            self.backing.len() == 1,
            "layer-separated layout has multiple allocations"
        );
        match &self.backing[0] {
            Backing::Memory(a) => Ok(a),
            Backing::File(_) => anyhow::bail!("disk is not an addressable allocation"),
        }
    }
    pub fn location(&self) -> StorageKind {
        match &self.backing[0] {
            Backing::Memory(a) if a.space() == MemorySpace::Device => {
                StorageKind::Device(self.identity.ordinal)
            }
            Backing::Memory(_) => StorageKind::Pinned,
            Backing::File(_) => StorageKind::Disk(0),
        }
    }

    /// # Safety
    /// Caller holds the block lease and excludes external engine accesses.
    pub unsafe fn packed_copy(
        &self,
        copies: &mut NativeCopies,
        block: usize,
        packed: &Allocation,
        to_host: bool,
    ) -> Result<()> {
        ensure!(
            packed.len() >= self.config.bytes_per_block() && packed.space() == MemorySpace::Host,
            "invalid staging buffer"
        );
        for (index, backing) in self.backing.iter().enumerate() {
            let packed_offset = index * self.plan.bytes_per_block();
            match backing {
                Backing::Memory(a) => unsafe {
                    copies.packed_copy(a, &self.plan, block, packed, packed_offset, to_host)?;
                },
                Backing::File(file) => {
                    let mut cursor = packed_offset;
                    for (offset, bytes) in self.plan.spans(block)? {
                        let ptr = (packed.address() + cursor) as *mut u8;
                        if to_host {
                            file.read_exact_at(
                                unsafe { std::slice::from_raw_parts_mut(ptr, bytes) },
                                offset as u64,
                            )?;
                        } else {
                            file.write_all_at(
                                unsafe { std::slice::from_raw_parts(ptr, bytes) },
                                offset as u64,
                            )?;
                        }
                        cursor += bytes;
                    }
                }
            }
        }
        Ok(())
    }
}

impl Layout for NativeLayout {
    fn config(&self) -> &LayoutConfig {
        &self.config
    }
    fn memory_regions(&self) -> &[Buffer] {
        &self.regions
    }
    fn memory_region(&self, block: usize, layer: usize, outer: usize) -> Result<MemoryRegion> {
        ensure!(
            block < self.config.num_blocks
                && layer < self.config.num_layers
                && outer < self.config.outer_dim,
            "layout index out of bounds"
        );
        ensure!(
            matches!(
                self.format,
                KvBlockLayout::OperationalNHD | KvBlockLayout::OperationalHND
            ),
            "universal layers are not contiguous; use native pack/unpack"
        );
        let chunk = if self.layer_separate.is_some() {
            layer
        } else {
            0
        };
        let Backing::Memory(allocation) = &self.backing[chunk] else {
            anyhow::bail!("disk has no device pointer")
        };
        let bytes = self.config.page_size * self.config.inner_dim * self.config.dtype_width_bytes;
        let offset = match self.layer_separate {
            None => {
                ((block * self.config.num_layers + layer) * self.config.outer_dim + outer) * bytes
            }
            Some(BlockDimension::BlockIsFirstDim) => {
                (block * self.config.outer_dim + outer) * bytes
            }
            Some(BlockDimension::BlockIsSecondDim) => {
                (outer * self.config.num_blocks + block) * bytes
            }
        };
        Ok(MemoryRegion {
            addr: allocation.address() + offset,
            size: bytes,
        })
    }
    fn required_allocations(&self) -> Vec<usize> {
        vec![self.plan.allocation_bytes(); self.backing.len()]
    }
    fn is_fully_contiguous(&self) -> bool {
        self.layer_separate.is_none()
    }
    fn num_blocks(&self) -> usize {
        self.config.num_blocks
    }
    fn num_layers(&self) -> usize {
        self.config.num_layers
    }
    fn outer_dim(&self) -> usize {
        self.config.outer_dim
    }
    fn page_size(&self) -> usize {
        self.config.page_size
    }
    fn inner_dim(&self) -> usize {
        self.config.inner_dim
    }
    fn dtype_width_bytes(&self) -> usize {
        self.config.dtype_width_bytes
    }
    fn block_layout(&self) -> KvBlockLayout {
        self.format
    }
    fn serialization_details(&self) -> LayoutTypeDetails {
        match self.layer_separate {
            Some(block_dim) => LayoutTypeDetails::LayerSeparate(LayerSeparateDetails {
                block_dim,
                kv_block_layout: self.format,
            }),
            None => LayoutTypeDetails::FullyContiguous(FullyContiguousDetails {
                block_format: BlockFormat::Operational,
                kv_block_layout: self.format,
            }),
        }
    }
}

pub struct NativeTransfers {
    identity: DeviceIdentity,
    copies: Arc<Mutex<NativeCopies>>,
    events: Arc<EventManager>,
    runtime: tokio::runtime::Handle,
    pending: Arc<tokio::sync::Semaphore>,
}

impl NativeTransfers {
    pub fn new(
        device: DeviceRuntime,
        events: Arc<EventManager>,
        runtime: tokio::runtime::Handle,
    ) -> Result<Self> {
        Ok(Self {
            identity: device.identity().clone(),
            copies: Arc::new(Mutex::new(NativeCopies::new(device)?)),
            events,
            runtime,
            pending: Arc::new(tokio::sync::Semaphore::new(64)),
        })
    }
    pub fn identity(&self) -> &DeviceIdentity {
        &self.identity
    }
    pub fn worker_id(&self) -> u64 {
        self.events.system_id()
    }
    pub fn execute(
        &self,
        src: &PhysicalLayout,
        dst: &PhysicalLayout,
        source: &[BlockId],
        destination: &[BlockId],
        options: TransferOptions,
    ) -> Result<TransferCompleteNotification> {
        let src = src.native_layout()?.clone();
        let dst = dst.native_layout()?.clone();
        ensure!(
            src.identity() == &self.identity && dst.identity() == &self.identity,
            "native transfer device/context mismatch"
        );
        ensure!(source.len() == destination.len(), "block counts differ");
        let mut a = src.config.clone();
        a.num_blocks = dst.config.num_blocks;
        ensure!(a == dst.config, "incompatible KV dimensions");
        ensure!(
            options.cuda_stream.is_none()
                && options.nixl_write_notification.is_none()
                && options.bounce_buffer.is_none()
                && options.src_kv_layout.is_none()
                && options.dst_kv_layout.is_none(),
            "native transfer does not accept CUDA/NIXL options or layout reinterpretation"
        );
        let layers = options.layer_range.unwrap_or(0..src.config.num_layers);
        ensure!(
            layers.start < layers.end && layers.end <= src.config.num_layers,
            "invalid layer range"
        );
        let mut unique = std::collections::HashSet::new();
        for (&s, &d) in source.iter().zip(destination) {
            ensure!(
                s < src.config.num_blocks && d < dst.config.num_blocks && unique.insert(d),
                "invalid or duplicate destination block"
            );
        }
        if Arc::ptr_eq(&src, &dst) {
            if source == destination {
                return Ok(TransferCompleteNotification::completed());
            }
            ensure!(
                source.iter().all(|id| !unique.contains(id)),
                "overlapping block remapping requires a separate layout"
            );
        } else {
            ensure!(
                !src.overlaps(&dst),
                "separate layouts must not alias allocations"
            );
        }
        if source.is_empty() {
            return Ok(TransferCompleteNotification::completed());
        }
        let permit = self
            .pending
            .clone()
            .try_acquire_owned()
            .map_err(|_| anyhow!("native transfer queue is full"))?;
        let event = self.events.new_event()?;
        let awaiter = self.events.awaiter(event.handle())?;
        let copies = self.copies.clone();
        let source = source.to_vec();
        let destination = destination.to_vec();
        self.runtime.spawn_blocking(move || {
            let _permit = permit;
            let mut copies = copies.lock();
            let result = transfer_blocks(&mut copies, &src, &dst, &source, &destination, layers);
            if result.is_err() && !copies.drain(Duration::from_secs(30)) {
                tracing::error!(
                    "native KVBM DMA did not drain; terminating before releasing block leases"
                );
                std::process::abort();
            }
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

fn transfer_blocks(
    copies: &mut NativeCopies,
    src: &NativeLayout,
    dst: &NativeLayout,
    source: &[usize],
    destination: &[usize],
    layers: Range<usize>,
) -> Result<()> {
    let bytes = src.config.bytes_per_block();
    let staging = copies.staging(bytes)?;
    let partial = layers != (0..src.config.num_layers);
    let previous = if partial {
        Some(copies.staging(bytes)?)
    } else {
        None
    };
    for (&s, &d) in source.iter().zip(destination) {
        unsafe { src.packed_copy(copies, s, &staging, true)? };
        if let Some(previous) = &previous {
            unsafe { dst.packed_copy(copies, d, previous, true)? };
            let layer_bytes = bytes / src.config.num_layers;
            let start = layers.start * layer_bytes;
            let count = layers.len() * layer_bytes;
            unsafe {
                std::ptr::copy_nonoverlapping(
                    (staging.address() + start) as *const u8,
                    (previous.address() + start) as *mut u8,
                    count,
                )
            };
            unsafe { dst.packed_copy(copies, d, previous, false)? };
        } else {
            unsafe { dst.packed_copy(copies, d, &staging, false)? };
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config() -> LayoutConfig {
        LayoutConfig::builder()
            .num_blocks(3)
            .num_layers(1)
            .outer_dim(2)
            .page_size(2)
            .inner_dim(4)
            .num_heads(Some(2))
            .dtype_width_bytes(2)
            .build()
            .unwrap()
    }

    #[test]
    fn block_second_hnd_uses_distinct_outer_allocations() {
        let plan = NativeLayout::plan(&config(), KvBlockLayout::OperationalHND, true).unwrap();
        assert_eq!(
            plan.spans(1).unwrap(),
            vec![
                (16, 4),
                (24, 4),
                (20, 4),
                (28, 4),
                (64, 4),
                (72, 4),
                (68, 4),
                (76, 4)
            ]
        );
        assert!(plan.spans(3).is_err());
    }

    #[test]
    fn invalid_layouts_fail_before_allocation() {
        let mut config = config();
        assert!(NativeLayout::plan(&config, KvBlockLayout::Unknown, false).is_err());
        let duplicate = KvBlockLayout::Custom([
            BlockDim::Layer,
            BlockDim::Outer,
            BlockDim::Head,
            BlockDim::Head,
        ]);
        assert!(NativeLayout::plan(&config, duplicate, false).is_err());
        config.num_blocks = usize::MAX;
        assert!(NativeLayout::plan(&config, KvBlockLayout::OperationalNHD, false).is_err());
        assert!(
            NativeLayout::from_allocations(config, KvBlockLayout::OperationalNHD, None, vec![])
                .is_err()
        );
    }
}
