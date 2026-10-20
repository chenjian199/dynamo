// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! SDK copy/pack fallback with explicit dense strides and owned completion events.

use anyhow::{Result, ensure};
use dynamo_platform::device::MemorySpace;
use dynamo_platform::memory::{Allocation, DeviceRuntime};
pub use dynamo_platform_runtime::kv::BlockLayout;
use dynamo_platform_runtime::kv::BlockTransfers;
use std::time::Duration;

pub struct NativeCopies {
    transfers: BlockTransfers,
}

impl NativeCopies {
    pub fn new(runtime: DeviceRuntime) -> Result<Self> {
        Ok(Self {
            transfers: BlockTransfers::new(runtime)?,
        })
    }

    pub fn staging(&self, bytes: usize) -> Result<Allocation> {
        Ok(self.transfers.allocate_host(bytes)?)
    }

    /// Pack or unpack one dense tensor block in logical axis order.
    ///
    /// # Safety
    /// The caller must hold exclusive block leases and wait for engine writes.
    /// On error, drain before releasing any participating block lease.
    #[allow(clippy::too_many_arguments)]
    pub unsafe fn packed_copy(
        &mut self,
        tensor: &Allocation,
        layout: &BlockLayout,
        block: usize,
        packed: &Allocation,
        offset: usize,
        to_host: bool,
    ) -> Result<()> {
        ensure!(
            tensor.identity() == packed.identity(),
            "staging belongs to another device/context"
        );
        ensure!(
            packed.space() == MemorySpace::Host,
            "staging must be pinned host memory"
        );
        ensure!(
            tensor.len() == layout.allocation_bytes(),
            "tensor/layout size mismatch"
        );
        ensure!(
            offset
                .checked_add(layout.bytes_per_block())
                .is_some_and(|end| end <= packed.len()),
            "staging range out of bounds"
        );
        if tensor.space() == MemorySpace::Device {
            unsafe {
                self.transfers
                    .copy_block(tensor, layout, block, packed, offset, to_host)?;
            }
        } else {
            ensure!(
                tensor.address().saturating_add(tensor.len()) <= packed.address()
                    || packed.address().saturating_add(packed.len()) <= tensor.address(),
                "staging must not alias the tensor"
            );
            let mut cursor = offset;
            for (position, bytes) in layout.spans(block)? {
                let (src, dst) = if to_host {
                    (tensor.address() + position, packed.address() + cursor)
                } else {
                    (packed.address() + cursor, tensor.address() + position)
                };
                unsafe { std::ptr::copy(src as *const u8, dst as *mut u8, bytes) };
                cursor += bytes;
            }
        }
        Ok(())
    }

    pub fn drain(&self, timeout: Duration) -> bool {
        self.transfers.drain(timeout)
    }
}
