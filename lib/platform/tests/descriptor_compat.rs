// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

use dynamo_platform::{CapabilityReport, CapabilityState, PlatformDescriptor};
use serde_json::json;
use std::collections::BTreeMap;

fn metax() -> serde_json::Value {
    json!({"schema_version":1,"platform_id":"metax","device_kind":"accelerator",
        "provider_version":"1","devices":[{"ordinal":0,"uuid":"device-uuid",
        "architecture":"C500","memory_bytes":1024}]})
}

#[test]
fn identity_roundtrip_preserves_optional_fields() {
    let mut wire = metax();
    wire["future_optional_field"] = json!(true);
    let platform: PlatformDescriptor = serde_json::from_value(wire).unwrap();
    assert_eq!(platform.platform_id, "metax");
    assert_eq!(platform.devices[0].numa_node, None);
    let roundtrip =
        serde_json::from_value::<PlatformDescriptor>(serde_json::to_value(&platform).unwrap())
            .unwrap();
    assert_eq!(roundtrip, platform);
    PlatformDescriptor::cpu().validate().unwrap();
}

#[test]
fn unsupported_or_inconsistent_identity_is_rejected() {
    for (key, value) in [
        ("schema_version", json!(2)),
        ("platform_id", json!("CUDA")),
        ("device_kind", json!("cpu")),
        ("devices", json!([])),
    ] {
        let mut wire = metax();
        wire[key] = value;
        assert!(
            serde_json::from_value::<PlatformDescriptor>(wire).is_err(),
            "{key}"
        );
    }
    let mut wire = metax();
    wire["devices"] = json!([wire["devices"][0], wire["devices"][0]]);
    assert!(serde_json::from_value::<PlatformDescriptor>(wire).is_err());
}

#[test]
fn required_capabilities_must_be_available_and_match_context() {
    let mut platform: PlatformDescriptor = serde_json::from_value(metax()).unwrap();
    let context = BTreeMap::from([("tp".into(), "1".into())]);
    for state in [
        CapabilityState::Unknown,
        CapabilityState::Unavailable,
        CapabilityState::Available,
    ] {
        platform.capabilities.insert(
            "engine.text".into(),
            CapabilityReport {
                state,
                provider: "vllm-metax".into(),
                reason: "test configuration".into(),
                conditions: context.clone(),
            },
        );
        assert_eq!(
            platform.require("engine.text", &context).is_ok(),
            state == CapabilityState::Available
        );
    }
    assert_eq!(
        platform
            .require("engine.text", &BTreeMap::new())
            .unwrap_err()
            .code,
        "capability_mismatch"
    );
    assert_eq!(
        platform.require("pd.transfer", &context).unwrap_err().code,
        "missing_capability"
    );
}
