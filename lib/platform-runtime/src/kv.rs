// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Bounded host-staging transfers for dense, explicitly described engine KV tensors.
//! This is a correctness fallback, not a fused layout kernel or a remote transport.

use dynamo_platform::PlatformError;
use dynamo_platform::device::{DeviceResult, MemorySpace};
use dynamo_platform::memory::{Allocation, DeviceRuntime, ExecutionStream};
use std::time::Duration;

fn invalid(message: &str) -> PlatformError {
    PlatformError {
        code: "invalid_kv_layout".into(),
        provider: "kvbm".into(),
        message: message.into(),
        retryable: false,
    }
}

/// A dense tensor's block axis and physical spans, in logical non-block axis order.
/// No inference from `max(shape[0], shape[1])` is permitted at this boundary.
#[derive(Debug, Clone)]
pub struct BlockLayout {
    blocks: usize,
    bytes_per_block: usize,
    allocation_bytes: usize,
    block_stride: usize,
    spans: Vec<(usize, usize)>,
}

impl BlockLayout {
    pub fn new(
        shape: &[usize],
        strides: &[usize],
        block_axis: usize,
        element_bytes: usize,
    ) -> DeviceResult<Self> {
        if shape.is_empty()
            || shape.len() != strides.len()
            || block_axis >= shape.len()
            || shape.contains(&0)
            || !matches!(element_bytes, 1 | 2 | 4 | 8)
        {
            return Err(invalid("invalid shape, block axis or element width"));
        }
        let mut axes: Vec<_> = (0..shape.len()).filter(|&i| shape[i] > 1).collect();
        axes.sort_by_key(|&i| strides[i]);
        let mut elements = 1usize;
        for i in axes {
            if strides[i] != elements {
                return Err(invalid("KV tensor must be dense and non-overlapping"));
            }
            elements = elements
                .checked_mul(shape[i])
                .ok_or_else(|| invalid("shape overflow"))?;
        }
        let allocation_bytes = elements
            .checked_mul(element_bytes)
            .filter(|&n| n <= isize::MAX as usize)
            .ok_or_else(|| invalid("tensor byte size overflow"))?;
        let block_stride = strides[block_axis]
            .checked_mul(element_bytes)
            .ok_or_else(|| invalid("stride overflow"))?;
        let blocks = shape[block_axis];
        let bytes_per_block = allocation_bytes / blocks;

        // Collapse the contiguous logical suffix before enumerating spans. Bound
        // the plan size so an unsupported layout cannot exhaust host memory.
        let mut run = element_bytes;
        let mut prefix = Vec::new();
        let mut contiguous = true;
        for i in (0..shape.len()).rev() {
            if i == block_axis {
                continue;
            }
            if contiguous && (shape[i] == 1 || strides[i].checked_mul(element_bytes) == Some(run)) {
                run *= shape[i];
            } else {
                contiguous = false;
                prefix.push(i);
            }
        }
        prefix.reverse();
        let count = bytes_per_block / run;
        if count > 65_536 {
            return Err(invalid(
                "KV layout requires more than 65536 copy spans per block",
            ));
        }
        let mut spans = Vec::with_capacity(count);
        for index in 0..count {
            let mut remaining = index;
            let mut offset = 0usize;
            for &axis in prefix.iter().rev() {
                offset += (remaining % shape[axis]) * strides[axis] * element_bytes;
                remaining /= shape[axis];
            }
            spans.push((offset, run));
        }
        Ok(Self {
            blocks,
            bytes_per_block,
            allocation_bytes,
            block_stride,
            spans,
        })
    }

    pub fn blocks(&self) -> usize {
        self.blocks
    }

    pub fn bytes_per_block(&self) -> usize {
        self.bytes_per_block
    }

    pub fn allocation_bytes(&self) -> usize {
        self.allocation_bytes
    }

    pub fn spans(&self, block: usize) -> DeviceResult<Vec<(usize, usize)>> {
        if block >= self.blocks {
            return Err(invalid("block index out of bounds"));
        }
        Ok(self
            .spans
            .iter()
            .map(|&(offset, size)| (offset + block * self.block_stride, size))
            .collect())
    }
}

/// A serialized copy stream. The enclosing block manager must hold block leases
/// until this operation returns successfully; an error poisons this executor.
pub struct BlockTransfers {
    runtime: DeviceRuntime,
    stream: ExecutionStream,
    poisoned: bool,
}

impl BlockTransfers {
    pub fn new(runtime: DeviceRuntime) -> DeviceResult<Self> {
        let stream = runtime.create_stream()?;
        Ok(Self {
            runtime,
            stream,
            poisoned: false,
        })
    }

    pub fn allocate_host(&self, bytes: usize) -> DeviceResult<Allocation> {
        self.runtime.allocate(bytes, MemorySpace::Host)
    }

    /// Copy a block to/from packed pinned host storage. All tensor owners are
    /// retained by the M04 completion queue even if a caller abandons its wait.
    ///
    /// # Safety
    /// The caller owns exclusive leases for both block ranges. Before offloading,
    /// it must wait for the engine's producing event. A failed operation must not
    /// release those leases until `drain` succeeds, or the process is terminated.
    pub unsafe fn copy_block(
        &mut self,
        tensor: &Allocation,
        layout: &BlockLayout,
        block: usize,
        host: &Allocation,
        host_offset: usize,
        to_host: bool,
    ) -> DeviceResult<()> {
        if self.poisoned {
            return Err(invalid("KV transfer executor is poisoned"));
        }
        if tensor.space() != MemorySpace::Device
            || host.space() != MemorySpace::Host
            || tensor.len() != layout.allocation_bytes
            || host_offset
                .checked_add(layout.bytes_per_block)
                .is_none_or(|end| end > host.len())
        {
            return Err(invalid("allocation space or size does not match KV layout"));
        }
        let spans = layout.spans(block)?;
        let mut packed = host_offset;
        // Multiple SDK copies share a stream; retain each real completion and
        // keep the submission group below the M04 runtime's in-flight bound.
        for chunk in spans.chunks(self.runtime.max_in_flight().min(32)) {
            let mut completions = Vec::with_capacity(chunk.len());
            for &(offset, bytes) in chunk {
                let (dst, dst_offset, src, src_offset) = if to_host {
                    (host, packed, tensor, offset)
                } else {
                    (tensor, offset, host, packed)
                };
                match unsafe {
                    self.runtime
                        .copy_async(&self.stream, dst, dst_offset, src, src_offset, bytes)
                } {
                    Ok(completion) => completions.push(completion),
                    Err(error) => {
                        self.poisoned = true;
                        return Err(error);
                    }
                }
                packed += bytes;
            }
            for event in completions {
                match event.wait(Duration::from_secs(30)) {
                    Ok(true) => {}
                    Ok(false) => {
                        self.poisoned = true;
                        return Err(invalid(
                            "KV copy completion timed out; leases must be retained",
                        ));
                    }
                    Err(error) => {
                        self.poisoned = true;
                        return Err(error);
                    }
                }
            }
        }
        Ok(())
    }

    pub fn drain(&self, timeout: Duration) -> bool {
        self.runtime.drain(timeout)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn block_axis_and_permutation_are_explicit() {
        let nhd = BlockLayout::new(&[2, 3, 4, 5], &[60, 20, 5, 1], 1, 2).unwrap();
        assert_eq!(nhd.spans(2).unwrap(), vec![(80, 40), (200, 40)]);
        // Logical NHD axes backed by HND physical storage.
        let hnd = BlockLayout::new(&[3, 4, 2, 5], &[40, 5, 20, 1], 0, 2).unwrap();
        assert_eq!(hnd.bytes_per_block, 80);
        assert_eq!(
            &hnd.spans(1).unwrap()[..3],
            &[(80, 10), (120, 10), (90, 10)]
        );
    }

    #[test]
    fn reject_aliases_holes_and_overflow() {
        for (shape, stride) in [
            (vec![2, 3], vec![0, 1]),
            (vec![2, 3], vec![4, 1]),
            (vec![usize::MAX, 2], vec![2, 1]),
        ] {
            assert!(BlockLayout::new(&shape, &stride, 0, 2).is_err());
        }
        let layout = BlockLayout::new(&[2, 3], &[3, 1], 0, 2).unwrap();
        assert!(layout.spans(2).is_err());
    }
}
