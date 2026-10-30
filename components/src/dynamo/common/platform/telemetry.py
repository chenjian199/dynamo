# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only, timestamped device telemetry. Unknown measurements are never zero.

Exporter schemas are explicit; utilization is a vendor-specific activity gauge,
not a cross-platform capacity estimate. No metric here changes Pod readiness or
writes a power cap. Engine queues/KV metrics remain the Planner's load inputs.
"""

import argparse
import importlib
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Literal

from dynamo.common.platform.registry import PlatformRegistry
from prometheus_client.parser import text_string_to_metric_families


@dataclass(frozen=True)
class Observation:
    value: float | None
    unit: str
    state: Literal["available", "unavailable", "stale", "error"]
    sampled_at: float | None
    collected_at: float
    source: str
    reason: str = ""
    timestamp_basis: str = "exporter"


def observation(
    value,
    *,
    unit,
    sampled_at,
    collected_at,
    source,
    max_age=30.0,
    timestamp_basis="exporter",
):
    if not math.isfinite(max_age) or max_age <= 0:
        raise ValueError("max_age must be finite and positive")
    if not math.isfinite(collected_at):
        raise ValueError("collected_at must be finite")
    state, reason = "available", ""
    if value is None:
        state, reason = "unavailable", "metric not exposed"
    elif not math.isfinite(value) or value < 0 or value >= 2**63 - 1024:
        state, reason = "error", "invalid or SDK sentinel value"
        value = None
    elif sampled_at is None:
        state, reason = "unavailable", "sample timestamp absent"
    elif not math.isfinite(sampled_at) or sampled_at > collected_at + 5:
        state, reason = "error", "invalid timestamp or clock skew"
    elif collected_at - sampled_at > max_age:
        state, reason = "stale", "sample exceeded max_age"
    if sampled_at is not None and not math.isfinite(sampled_at):
        sampled_at = None
        state, reason = "error", "invalid timestamp"
    return Observation(
        value, unit, state, sampled_at, collected_at, source, reason, timestamp_basis
    )


# MB in these installed exporter memory fields means MiB, checked against the
# device's capacity. Original metric names/values remain available as evidence.
SCHEMAS = {
    "cuda": {
        "memory.used": ("DCGM_FI_DEV_FB_USED", "bytes", 2**20),
        "memory.available": ("DCGM_FI_DEV_FB_FREE", "bytes", 2**20),
        "utilization": ("DCGM_FI_DEV_GPU_UTIL", "percent", 1),
        "power.measured": ("DCGM_FI_DEV_POWER_USAGE", "W", 1),
        "health.error_code": ("DCGM_FI_DEV_XID_ERRORS", "vendor_code", 1),
    },
    "ascend": {
        "memory.used": ("npu_chip_info_hbm_used_memory", "bytes", 2**20),
        "memory.total": ("npu_chip_info_hbm_total_memory", "bytes", 2**20),
        "utilization": ("npu_chip_info_utilization", "percent", 1),
        "power.measured": ("npu_chip_info_power", "W", 1),
        "health.healthy": ("npu_chip_info_health_status", "bool", 1),
        "health.error_code": ("npu_chip_info_error_code", "vendor_code", 1),
    },
}


def normalize_exporter(text, platform_id, *, collected_at=None, max_age=30.0):
    """Normalize a node-local exporter without assuming host ordinal == Pod ID."""
    if platform_id not in SCHEMAS:
        raise ValueError(f"No validated exporter schema for {platform_id}")
    collected_at = time.time() if collected_at is None else collected_at
    schema = SCHEMAS[platform_id]
    reverse = {v[0]: (k, v[1], v[2]) for k, v in schema.items()}
    devices = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name not in reverse:
                continue
            identity = (
                sample.labels.get("UUID")
                if platform_id == "cuda"
                else sample.labels.get("vdie_id")
            )
            if not identity:
                raise ValueError(f"{sample.name}: stable device identity missing")
            device = devices.setdefault(identity, {"identity": identity, "metrics": {}})
            name, unit, scale = reverse[sample.name]
            if name in device["metrics"]:
                raise ValueError(f"Ambiguous duplicate series for {identity}/{name}")
            # DCGM commonly omits per-sample timestamps. Scrape-time freshness
            # means reachability of the exporter, not proof its SDK cache is fresh.
            timestamp = (
                float(sample.timestamp)
                if sample.timestamp is not None
                else collected_at
            )
            basis = "exporter" if sample.timestamp is not None else "scrape"
            value = sample.value
            if (unit == "percent" and not 0 <= value <= 100) or (
                unit == "bool" and value not in (0, 1)
            ):
                value = math.nan
            device["metrics"][name] = asdict(
                observation(
                    value * scale,
                    unit=unit,
                    sampled_at=timestamp,
                    collected_at=collected_at,
                    source=sample.name,
                    max_age=max_age,
                    timestamp_basis=basis,
                )
            )
    for device in devices.values():
        for name, (source, unit, _) in schema.items():
            device["metrics"].setdefault(
                name,
                asdict(
                    observation(
                        None,
                        unit=unit,
                        sampled_at=None,
                        collected_at=collected_at,
                        source=source,
                    )
                ),
            )
        metrics = device["metrics"]
        operands = (
            ("memory.used", "memory.available")
            if platform_id == "cuda"
            else ("memory.total", "memory.used")
        )
        output = "memory.total" if platform_id == "cuda" else "memory.available"
        left, right = (metrics[key] for key in operands)
        value = None
        if left["state"] == right["state"] == "available":
            value = (
                left["value"] + right["value"]
                if platform_id == "cuda"
                else left["value"] - right["value"]
            )
        metrics[output] = asdict(
            observation(
                value,
                unit="bytes",
                sampled_at=(
                    min(left["sampled_at"], right["sampled_at"])
                    if value is not None
                    else None
                ),
                collected_at=collected_at,
                source="derived from " + ",".join(operands),
                max_age=max_age,
                timestamp_basis=left["timestamp_basis"],
            )
        )
        # XID=0 only says no recorded XID; it is not comprehensive hardware health.
        healthy = metrics.get("health.healthy", {})
        device["health"] = {
            "severity": (
                ("ok" if healthy.get("value") == 1 else "critical")
                if healthy.get("state") == "available"
                else "unknown"
            ),
            "reason": (
                "exporter health gauge"
                if healthy.get("state") == "available"
                else "no current comprehensive health gauge"
            ),
            "error_code": metrics["health.error_code"],
        }
    return {
        "schema_version": 1,
        "platform_id": platform_id,
        "collected_at": collected_at,
        "devices": list(devices.values()),
        "power_control": "unavailable",
    }


def collect_exporter(url, platform_id, *, max_age=30.0):
    """Read one bounded endpoint; network failures are explicit, not idle devices."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("Exporter URL must use HTTP(S)")
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("Exporter response exceeds 8 MiB")
        return normalize_exporter(data.decode(), platform_id, max_age=max_age)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {
            "schema_version": 1,
            "platform_id": platform_id,
            "state": "error",
            "reason": str(error),
            "collected_at": time.time(),
            "devices": [],
            "power_control": "unavailable",
        }


def collect_local(platform_id):
    """Visible-device memory from the selected runtime; no ordinal remapping.

    Vendor SDK import is intentionally lazy: CPU control-plane users of exporter
    normalization must not need a CUDA/MACA/CANN runtime or a device allocation.
    """
    descriptor = PlatformRegistry().prepare(platform_id)
    now = time.time()
    if platform_id == "cpu":
        return {
            "schema_version": 1,
            "platform_id": "cpu",
            "devices": [],
            "collected_at": now,
            "power_control": "unavailable",
        }
    torch = importlib.import_module("torch")
    runtime = torch.npu if platform_id == "ascend" else torch.cuda
    devices = []
    for device in descriptor["devices"]:
        free, total = runtime.mem_get_info(device["ordinal"])
        metrics = {}
        for name, value in (
            ("memory.available", free),
            ("memory.total", total),
            ("memory.used", total - free),
        ):
            metrics[name] = asdict(
                observation(
                    value,
                    unit="bytes",
                    sampled_at=now,
                    collected_at=now,
                    source=f"{platform_id}.mem_get_info",
                    timestamp_basis="query",
                )
            )
        for name, unit in (("utilization", "percent"), ("power.measured", "W")):
            metrics[name] = asdict(
                observation(
                    None,
                    unit=unit,
                    sampled_at=None,
                    collected_at=now,
                    source="not queried",
                )
            )
        devices.append(
            {
                "identity": device["uuid"],
                "ordinal": device["ordinal"],
                "architecture": device["architecture"],
                "metrics": metrics,
                "health": {
                    "severity": "unknown",
                    "reason": "memory query is not a health check",
                },
            }
        )
    return {
        "schema_version": 1,
        "platform_id": platform_id,
        "software": descriptor["software"],
        "devices": devices,
        "collected_at": now,
        "power_control": "unavailable",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", choices=["cuda", "ascend", "metax", "cpu"], required=True
    )
    parser.add_argument(
        "--url", help="Exporter URL; omitted uses visible local devices"
    )
    parser.add_argument("--max-age", type=float, default=30)
    args = parser.parse_args()
    print(
        json.dumps(
            (
                collect_exporter(args.url, args.platform, max_age=args.max_age)
                if args.url
                else collect_local(args.platform)
            ),
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
