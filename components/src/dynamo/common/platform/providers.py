# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Built-in probes. Vendor modules are imported only by the selected provider."""

import importlib
import importlib.metadata
import re

from dynamo.common.platform.registry import PlatformError
from dynamo.common.platform.versions import validate_vllm_versions


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
    if platform_id == "ascend":
        caps["gms.vmm"] = capability(
            "unavailable",
            platform_id,
            "tested 910B3/CANN 9.1/HDK 26.1.1 rejects read-only VMM access "
            "and POSIX allocation; GMS provider unavailable",
        )
    elif context.get("gms") == "true":
        try:
            software["gpu-memory-service"] = importlib.metadata.version(
                "gpu-memory-service"
            )
            from gpu_memory_service.common.vmm import VMMDeviceType

            provider_present = platform_id in {kind.value for kind in VMMDeviceType}
        except (ImportError, importlib.metadata.PackageNotFoundError):
            provider_present = False
        eligible = provider_present and platform_id in ("cuda", "metax")
        caps["gms.vmm"] = capability(
            "available" if eligible else "unavailable",
            platform_id,
            (
                "GMS provider installed; device VMM, permissions and socket identity checked at use"
                if eligible
                else "install the platform-enabled gpu-memory-service package"
            ),
            context,
        )
    if context.get("engine") == "vllm":
        software.update(validate_vllm_versions(platform_id))
        # This describes an installed backend, not a matrix of certified model
        # topologies. vLLM validates TP/PP/DP, graph execution and model support.
        task = context.get("task", "text")
        supported = task in ("text", "multimodal", "embedding")
        caps[f"engine.{task}"] = capability(
            "available" if supported else "unknown",
            platform_id,
            (
                "platform backend installed; requested topology and model support are validated by vLLM at initialization"
                if supported
                else "configuration outside current platform integration scope"
            ),
            context,
        )
        if context.get("kvbm") == "true":
            software["kvbm"] = importlib.metadata.version("kvbm")
            eligible = (
                supported
                and context.get("eager") == "true"
                and context.get("task") == "text"
                and context.get("tp") in ("1", "2")
                and context.get("dp") == "1"
            )
            caps["kvbm.offload"] = capability(
                "available" if eligible else "unavailable",
                platform_id,
                "local dense KVBM TP1/TP2 DP1; tensor registration and storage readiness checked at startup",
                context,
            )
        kv = supported and context.get("kv_events") == "true"
        pd = (
            supported
            and context.get("eager") == "true"
            and context.get("task") == "text"
            and context.get("tp", "").isdigit()
            and int(context["tp"]) > 0
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
                "engine-native TP DP1 PD configuration; connector initialization, TP layout negotiation and transfer readiness required",
                context,
            )
        caps["kv.events"] = capability(
            "available" if kv else "unavailable",
            platform_id,
            (
                "vLLM ZMQ events and prefix caching enabled"
                if kv
                else "requires supported aggregate configuration and explicit KV events"
            ),
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
                (
                    "requested aggregate engine integration; requires successful model initialization"
                    if supported and enabled
                    else "not enabled in this configuration"
                ),
                context,
            )
    return caps
