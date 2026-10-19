# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Built-in probes. Vendor modules are imported only by the selected provider."""

import importlib
import importlib.metadata
import re

from dynamo.common.platform.registry import PlatformError


def capability(state, provider, reason, conditions=None):
    return dict(
        state=state, provider=provider, reason=reason, conditions=conditions or {}
    )


def cpu(context):
    return dict(
        schema_version=1,
        platform_id="cpu",
        device_kind="cpu",
        provider_version="1",
        software={},
        devices=[],
        capabilities={
            "runtime.control": capability(
                "available", "cpu", "CPU control plane requires no accelerator SDK"
            )
        },
    )


def _accelerator(platform_id, context):
    torch = importlib.import_module("torch")
    version = importlib.metadata.version("torch")
    if ("metax" in version.lower()) != (platform_id == "metax"):
        raise PlatformError("stack_mismatch", platform_id, f"torch build is {version}")
    if not torch.cuda.is_available():
        raise PlatformError("missing_device", platform_id, "no visible accelerator")
    software = {"torch": version}
    devices = []
    for ordinal in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(ordinal)
        uuid = str(getattr(props, "uuid", ""))
        if not uuid:
            raise PlatformError(
                "missing_device_identity",
                platform_id,
                "UUID unavailable; ordinal is not a global identity",
            )
        pci = None
        if all(
            hasattr(props, key)
            for key in ("pci_domain_id", "pci_bus_id", "pci_device_id")
        ):
            pci = f"{props.pci_domain_id:04x}:{props.pci_bus_id:02x}:{props.pci_device_id:02x}.0"
        devices.append(
            dict(
                ordinal=ordinal,
                uuid=uuid,
                architecture=props.name,
                memory_bytes=props.total_memory,
                pci_bus_id=pci,
            )
        )
    if platform_id == "metax":
        software["vllm-metax"] = importlib.metadata.version("vllm-metax")
        sdk = re.search(r"\.maca(.*?)\.torch", software["vllm-metax"])
        if sdk:
            software["maca_build"] = sdk[1]
        management = importlib.import_module("pymxsml")
        management.mxSmlInit()
        # This SDK exposes process-wide initialization, with no shutdown API.
        software["maca_runtime"] = str(management.mxSmlGetMacaVersion())
        drivers = {
            str(management.mxSmlGetDeviceVersion(i, management.MXSML_VERSION_DRIVER))
            for i in range(management.mxSmlGetDeviceCount())
        }
        software["driver"] = ",".join(sorted(drivers))

    else:
        if not torch.version.cuda or torch.version.hip:
            raise PlatformError(
                "stack_mismatch", platform_id, "requires NVIDIA CUDA PyTorch"
            )
        software["cuda_build"] = torch.version.cuda
    return dict(
        schema_version=1,
        platform_id=platform_id,
        device_kind="accelerator",
        provider_version="1",
        software=software,
        devices=devices,
        capabilities=build_capabilities(platform_id, context, software),
    )


def metax(context):
    return _accelerator("metax", context)


def cuda(context):
    return _accelerator("cuda", context)


def build_capabilities(platform_id, context, software):
    """Describe configuration eligibility; serving readiness is checked by the engine."""
    caps = {
        "device.compute": capability(
            "available",
            platform_id,
            "device identity, model and memory queried through selected provider",
        ),
        **{
            name: capability("unknown", platform_id, "not probed or certified by M01")
            for name in (
                "pd.transfer",
                "device.rdma",
                "gms.vmm",
                "power.control",
                "kvbm.offload",
            )
        },
    }
    if context.get("engine") == "vllm":
        software["vllm"] = importlib.metadata.version("vllm")
        expected = {"metax": "0.26.0", "ascend": "0.23.0"}.get(platform_id)
        if expected is not None and (
            software["vllm"].split("+")[0] != expected
            or software[f"vllm-{platform_id}"].split("+")[0] != expected
        ):
            raise PlatformError(
                "unsupported_stack",
                platform_id,
                f"validated vLLM/{platform_id} combination is {expected}",
            )
        supported = (
            context.get("task") in ("text", "multimodal")
            and context.get("tp") in ("1", "2", "4")
            and context.get("dp") in ("1", "2", "4")
            and context.get("eager") == "true"
        )
        caps[
            "engine.multimodal"
            if context.get("task") == "multimodal"
            else "engine.text"
        ] = capability(
            "available" if supported else "unknown",
            platform_id,
            "eager stack with requested TP/DP; readiness and model support still require engine initialization"
            if supported
            else "configuration outside current platform integration scope",
            context,
        )
        kv = supported and context.get("kv_events") == "true"
        pd = (
            supported
            and context.get("task") == "text"
            and context.get("tp") in ("1", "2")
            and context.get("dp") == "1"
            and context.get("pd_connector")
            in (
                ("MooncakeConnector", "NixlConnector")
                if platform_id == "metax"
                else (
                    ("MooncakeConnector",)
                    if platform_id == "ascend"
                    else ("NixlConnector",)
                )
            )
        )
        if pd:
            caps["pd.transfer"] = capability(
                "available",
                platform_id,
                "engine-native TP1/TP2 DP1 PD configuration; connector initialization and transfer readiness required",
                context,
            )
        caps["kv.events"] = capability(
            "available" if kv else "unavailable",
            platform_id,
            "vLLM ZMQ events and prefix caching enabled"
            if kv
            else "requires supported aggregate configuration and explicit KV events",
            context,
        )
        for name, enabled in (
            ("engine.lora", context.get("lora") == "true"),
            ("engine.quantization", context.get("quantization", "none") != "none"),
            ("engine.speculative", context.get("speculative", "null") != "null"),
            ("engine.expert_parallel", context.get("expert_parallel") == "true"),
        ):
            caps[name] = capability(
                "available" if supported and enabled else "unavailable",
                platform_id,
                "requested aggregate engine integration; requires successful model initialization"
                if supported and enabled
                else "not enabled in this configuration",
                context,
            )
    return caps
