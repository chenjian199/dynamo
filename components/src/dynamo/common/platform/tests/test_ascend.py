# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import ctypes
from types import SimpleNamespace

import pytest

from dynamo.common.platform import ascend as provider
from dynamo.common.platform.registry import PlatformError

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


def test_ascend_uses_physical_identity_and_preserves_stack_boundary(monkeypatch):
    loaded, physical_ids = [], []
    properties = SimpleNamespace(
        name="Ascend910B3",
        total_memory=1024,
        uuid="00000000-0000-0000-0000-000000000000",
    )
    modules = {
        "torch": SimpleNamespace(
            npu=SimpleNamespace(
                is_available=lambda: True,
                device_count=lambda: 2,
                get_device_properties=lambda ordinal: properties,
            )
        ),
        "torch_npu": object(),
        "acl": SimpleNamespace(
            rt=SimpleNamespace(
                get_phy_dev_id_by_logic_dev_id=lambda ordinal: ([3, 1][ordinal], 0)
            )
        ),
    }

    def load(name):
        loaded.append(name)
        return modules[name]

    class Dcmi:
        def identity(self, physical_id):
            physical_ids.append(physical_id)
            return f"ascend-die:chip-{physical_id}", f"0000:8{physical_id}:00.0"

    versions = {
        "torch": "2.10.0+cpu",
        "torch-npu": "2.10.0.post4",
        "vllm-ascend": "0.23.0",
        "vllm": "0.23.0+empty",
    }
    monkeypatch.setattr(provider.importlib, "import_module", load)
    monkeypatch.setattr(provider.importlib.metadata, "version", versions.__getitem__)
    monkeypatch.setattr(provider, "_Dcmi", Dcmi)
    monkeypatch.setattr(
        provider,
        "_version",
        lambda path, key: "25.2.0" if key == "Version" else "9.1.0",
    )
    context = dict(
        engine="vllm", task="text", tp="1", dp="1", eager="true", kv_events="true"
    )
    descriptor = provider.ascend(context)
    assert loaded == ["torch", "torch_npu", "acl"]
    assert physical_ids == [3, 1]
    assert [d["ordinal"] for d in descriptor["devices"]] == [0, 1]
    assert [d["uuid"] for d in descriptor["devices"]] == [
        "ascend-die:chip-3",
        "ascend-die:chip-1",
    ]
    assert descriptor["capabilities"]["pd.transfer"]["state"] == "unknown"
    assert descriptor["capabilities"]["kv.events"]["state"] == "available"
    versions["vllm"] = "0.24.0"
    with pytest.raises(PlatformError, match="unsupported_stack"):
        provider.ascend(context)
    modules["acl"].rt.get_phy_dev_id_by_logic_dev_id = lambda ordinal: (0, 207000)
    with pytest.raises(PlatformError, match="207000"):
        provider.ascend(context)


def test_dcmi_rejects_zero_die_identity():
    dcmi = object.__new__(provider._Dcmi)
    dcmi.library = SimpleNamespace(
        dcmi_get_card_id_device_id_from_phyid=lambda *args: 0,
        dcmi_get_device_die=lambda *args: 0,
    )
    with pytest.raises(PlatformError, match="missing_device_identity"):
        dcmi.identity(0)
    assert ctypes.sizeof(provider._DieId) == 20
    assert ctypes.sizeof(provider._PcieInfo) == 64
