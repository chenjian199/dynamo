// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

pub mod device;
pub mod memory;

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

pub const SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceKind {
    Cpu,
    Accelerator,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeviceDescriptor {
    pub ordinal: u32,
    pub uuid: String,
    pub architecture: String,
    pub memory_bytes: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pci_bus_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub numa_node: Option<u32>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityState {
    Available,
    Unavailable,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityReport {
    pub state: CapabilityState,
    pub provider: String,
    pub reason: String,
    #[serde(default)]
    pub conditions: BTreeMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, thiserror::Error)]
#[error("{provider}: {code}: {message}")]
pub struct PlatformError {
    pub code: String,
    pub provider: String,
    pub message: String,
    pub retryable: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "PlatformWire")]
pub struct PlatformDescriptor {
    pub schema_version: u32,
    pub platform_id: String,
    pub device_kind: DeviceKind,
    pub provider_version: String,
    pub software: BTreeMap<String, String>,
    pub devices: Vec<DeviceDescriptor>,
    pub capabilities: BTreeMap<String, CapabilityReport>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub certification: Option<String>,
}

#[derive(Deserialize)]
struct PlatformWire {
    schema_version: u32,
    platform_id: String,
    device_kind: DeviceKind,
    provider_version: String,
    #[serde(default)]
    software: BTreeMap<String, String>,
    #[serde(default)]
    devices: Vec<DeviceDescriptor>,
    #[serde(default)]
    capabilities: BTreeMap<String, CapabilityReport>,
    #[serde(default)]
    certification: Option<String>,
}

impl TryFrom<PlatformWire> for PlatformDescriptor {
    type Error = PlatformError;

    fn try_from(w: PlatformWire) -> Result<Self, Self::Error> {
        let descriptor = Self {
            schema_version: w.schema_version,
            platform_id: w.platform_id,
            device_kind: w.device_kind,
            provider_version: w.provider_version,
            software: w.software,
            devices: w.devices,
            capabilities: w.capabilities,
            certification: w.certification,
        };
        descriptor.validate()?;
        Ok(descriptor)
    }
}

impl PlatformDescriptor {
    pub fn cpu() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            platform_id: "cpu".into(),
            device_kind: DeviceKind::Cpu,
            provider_version: "1".into(),
            software: BTreeMap::new(),
            devices: Vec::new(),
            capabilities: BTreeMap::from([(
                "runtime.control".into(),
                CapabilityReport {
                    state: CapabilityState::Available,
                    provider: "cpu".into(),
                    reason: "CPU control plane requires no accelerator SDK".into(),
                    conditions: BTreeMap::new(),
                },
            )]),
            certification: None,
        }
    }

    pub fn error(&self, code: &str, message: impl Into<String>) -> PlatformError {
        PlatformError {
            code: code.into(),
            provider: self.platform_id.clone(),
            message: message.into(),
            retryable: false,
        }
    }

    pub fn validate(&self) -> Result<(), PlatformError> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(self.error("unsupported_schema", "requires platform schema 1"));
        }
        let valid_id = !self.platform_id.is_empty()
            && self.platform_id.len() <= 64
            && self
                .platform_id
                .bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-');
        if !valid_id || self.provider_version.is_empty() {
            return Err(self.error(
                "invalid_descriptor",
                "invalid platform ID or provider version",
            ));
        }
        if (self.platform_id == "cpu") != (self.device_kind == DeviceKind::Cpu) {
            return Err(self.error(
                "invalid_descriptor",
                "CPU platform and device kind disagree",
            ));
        }
        let mut ordinals = BTreeSet::new();
        let mut uuids = BTreeSet::new();
        for device in &self.devices {
            if device.uuid.is_empty()
                || device.architecture.is_empty()
                || device.memory_bytes == 0
                || !ordinals.insert(device.ordinal)
                || !uuids.insert(&device.uuid)
            {
                return Err(self.error(
                    "invalid_device",
                    "missing or duplicate device identity/capacity",
                ));
            }
        }
        if self.device_kind == DeviceKind::Accelerator && self.devices.is_empty() {
            return Err(self.error(
                "missing_device",
                "accelerator platform requires probed devices",
            ));
        }
        for (name, capability) in &self.capabilities {
            if name.is_empty() || capability.provider.is_empty() || capability.reason.is_empty() {
                return Err(self.error(
                    "invalid_capability",
                    "capability needs an ID, provider and evidence/reason",
                ));
            }
        }
        Ok(())
    }

    pub fn require(
        &self,
        capability: &str,
        context: &BTreeMap<String, String>,
    ) -> Result<(), PlatformError> {
        let report = self
            .capabilities
            .get(capability)
            .filter(|r| r.state == CapabilityState::Available)
            .ok_or_else(|| {
                self.error(
                    "missing_capability",
                    format!("required capability {capability} is not available"),
                )
            })?;
        if report
            .conditions
            .iter()
            .any(|(key, value)| context.get(key) != Some(value))
        {
            return Err(self.error(
                "capability_mismatch",
                format!("conditions for {capability} do not match"),
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod memory_tests;
