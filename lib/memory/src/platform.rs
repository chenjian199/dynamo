// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Provider-owned memory usable by Buffer, OffsetBuffer and ArenaAllocator.
//! SDK ownership is retained by the allocation, including borrowed engine owners.
//! Transfer-specific registration is intentionally delegated to M05 adapters.

use crate::{MemoryDescriptor, StorageKind};
use dynamo_platform::device::{DeviceIdentity, MemorySpace};
use dynamo_platform::memory::Allocation;
use std::any::Any;

/// A reference-counted allocation with explicit provider/context identity.
#[derive(Debug, Clone)]
pub struct PlatformStorage(Allocation);

impl PlatformStorage {
    /// Preserve the allocation's owner and registration lifetime.
    pub fn new(allocation: Allocation) -> Self {
        Self(allocation)
    }
    /// Allocation used for checked asynchronous copies.
    pub fn allocation(&self) -> &Allocation {
        &self.0
    }
}

impl MemoryDescriptor for PlatformStorage {
    fn addr(&self) -> usize {
        self.0.address()
    }
    fn size(&self) -> usize {
        self.0.len()
    }
    fn storage_kind(&self) -> StorageKind {
        match self.0.space() {
            MemorySpace::Host => StorageKind::Pinned,
            MemorySpace::Device => StorageKind::Device(self.0.identity().ordinal),
        }
    }
    fn device_identity(&self) -> Option<&DeviceIdentity> {
        Some(self.0.identity())
    }
    fn as_any(&self) -> &dyn Any {
        self
    }
    #[cfg(feature = "nixl")]
    fn nixl_descriptor(&self) -> Option<crate::nixl::NixlDescriptor> {
        None
    }
}
