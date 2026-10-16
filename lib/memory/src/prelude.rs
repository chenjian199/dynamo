// SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// Derived from NVIDIA Dynamo. Modified by HEYI.

pub use super::MemoryDescriptor;
#[cfg(feature = "nixl")]
pub use super::nixl::{NixlCompatible, NixlMemory, NixlRegisterExt, RegisteredView};
pub use super::tensor::{TensorDescriptor, TensorDescriptorExt};
