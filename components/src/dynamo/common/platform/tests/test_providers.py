# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace

import pytest

from dynamo.common.platform import providers
from dynamo.common.platform.registry import PlatformError

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


@pytest.mark.parametrize("platform_id", ["cuda", "metax"])
def test_selected_provider_only_and_device_identity(monkeypatch, platform_id):
    loaded = []
    props = SimpleNamespace(
        uuid="stable-uuid",
        name="device model",
        total_memory=1024,
        pci_domain_id=0,
        pci_bus_id=86,
        pci_device_id=0,
    )
    torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_properties=lambda ordinal: props,
        ),
        version=SimpleNamespace(cuda="13.0", hip=None),
    )
    sdk = SimpleNamespace(
        mxSmlInit=lambda: None,
        mxSmlGetMacaVersion=lambda: "3.8.2.6",
        mxSmlGetDeviceCount=lambda: 1,
        MXSML_VERSION_DRIVER=0,
        mxSmlGetDeviceVersion=lambda i, unit: "3.8.30",
    )

    def load(name):
        loaded.append(name)
        return {"torch": torch, "pymxsml": sdk}[name]

    monkeypatch.setattr(providers.importlib, "import_module", load)
    versions = {
        "torch": "2.10.0+metax3.8.2.2" if platform_id == "metax" else "2.10.0+cu130",
        "vllm": "0.26.0" if platform_id == "metax" else "0.28.0",
        "vllm-metax": "0.26.0+maca3.8.2.208.torch2.10.0",
    }
    monkeypatch.setattr(providers.importlib.metadata, "version", versions.__getitem__)
    context = {
        "engine": "vllm",
        "task": "text",
        "tp": "1",
        "dp": "1",
        "eager": "true",
        "kv_events": "true",
    }
    descriptor = getattr(providers, platform_id)(context)
    assert loaded == (["torch", "pymxsml"] if platform_id == "metax" else ["torch"])
    assert descriptor["devices"][0]["uuid"] == "stable-uuid"
    assert descriptor["devices"][0]["pci_bus_id"] == "0000:56:00.0"
    assert descriptor["capabilities"]["pd.transfer"]["state"] == "unknown"
    assert descriptor["capabilities"]["engine.text"]["state"] == "available"
    context["tp"] = "2"
    assert (
        getattr(providers, platform_id)(context)["capabilities"]["engine.text"]["state"]
        == "unknown"
    )
    props.uuid = ""
    with pytest.raises(PlatformError, match="missing_device_identity"):
        getattr(providers, platform_id)(context)


def test_metax_torch_cannot_be_registered_as_cuda(monkeypatch):
    monkeypatch.setattr(providers.importlib, "import_module", lambda name: object())
    monkeypatch.setattr(
        providers.importlib.metadata, "version", lambda name: "2.10.0+metax3.8.2.2"
    )
    with pytest.raises(PlatformError, match="stack_mismatch"):
        providers.cuda({})
