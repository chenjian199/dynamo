// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Byte-exact NHD/HND offload/reload checks using the M06 native copy path.
use dynamo_platform::{
    device::MemorySpace::{Device, Host},
    memory::DeviceRuntime,
};
use dynamo_platform_runtime::{
    NativeBackend,
    kv::{BlockLayout, BlockTransfers},
};
use std::time::Duration;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let platform = std::env::args().nth(1).ok_or("expected platform")?;
    let device = std::env::args()
        .nth(2)
        .unwrap_or_else(|| "0".into())
        .parse::<u32>()?;
    let runtime = DeviceRuntime::new(NativeBackend::open(&platform, device)?, 64)?;
    let mut copies = BlockTransfers::new(runtime.clone())?;
    let stream = runtime.create_stream()?;
    let mut cases = 0;
    for tokens in [16, 32, 64] {
        for element_bytes in [1, 2, 4] {
            let shape = [2, 7, tokens, 4, 32];
            for stride in [
                [7 * tokens * 128, tokens * 128, 128, 32, 1],
                [7 * tokens * 128, tokens * 128, 32, tokens * 32, 1],
            ] {
                let layout = BlockLayout::new(&shape, &stride, 1, element_bytes)?;
                let bytes = layout.allocation_bytes();
                let input = runtime.allocate(bytes, Host)?;
                let output = runtime.allocate(bytes, Host)?;
                let tensor = runtime.allocate(bytes, Device)?;
                let packed = copies.allocate_host(layout.bytes_per_block() * layout.blocks())?;
                let expected: Vec<_> = (0..bytes)
                    .map(|i| ((i * 17 + i / 251) % 251) as u8)
                    .collect();
                for _ in 0..4 {
                    unsafe {
                        input.write(0, &expected)?;
                        assert!(
                            runtime
                                .copy_async(&stream, &tensor, 0, &input, 0, bytes)?
                                .wait(Duration::from_secs(30))?
                        );
                        for block in 0..layout.blocks() {
                            copies.copy_block(
                                &tensor,
                                &layout,
                                block,
                                &packed,
                                block * layout.bytes_per_block(),
                                true,
                            )?;
                        }
                        // Check the packed representation independently from the
                        // reload mapping, so equal inverse bugs cannot cancel out.
                        let mut actual = vec![0; bytes];
                        packed.read(0, &mut actual)?;
                        let mut offset = 0;
                        for block in 0..7 {
                            for outer in 0..2 {
                                for token in 0..tokens {
                                    for head in 0..4 {
                                        for dim in 0..32 {
                                            let src = (outer * stride[0]
                                                + block * stride[1]
                                                + token * stride[2]
                                                + head * stride[3]
                                                + dim)
                                                * element_bytes;
                                            assert_eq!(
                                                &actual[offset..offset + element_bytes],
                                                &expected[src..src + element_bytes]
                                            );
                                            offset += element_bytes;
                                        }
                                    }
                                }
                            }
                        }
                        input.write(0, &vec![0; bytes])?;
                        assert!(
                            runtime
                                .copy_async(&stream, &tensor, 0, &input, 0, bytes)?
                                .wait(Duration::from_secs(30))?
                        );
                        for block in (0..layout.blocks()).rev() {
                            copies.copy_block(
                                &tensor,
                                &layout,
                                block,
                                &packed,
                                block * layout.bytes_per_block(),
                                false,
                            )?;
                        }
                        assert!(
                            runtime
                                .copy_async(&stream, &output, 0, &tensor, 0, bytes)?
                                .wait(Duration::from_secs(30))?
                        );
                        output.read(0, &mut actual)?;
                        assert_eq!(actual, expected);
                    }
                }
                cases += 1;
            }
        }
    }
    assert!(copies.drain(Duration::from_secs(30)));
    println!(
        "platform={platform} device={device} passed=true layouts={cases} rounds_per_layout=4 independent_pack_parity=true"
    );
    Ok(())
}
