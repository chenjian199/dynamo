# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Derived from NVIDIA Dynamo. Modified by HEYI.

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import torch

try:
    from dynamo.vllm.omni.connectors import nixl_connector as nixl_module
except ImportError:
    pytest.skip("vLLM omni dependencies not available", allow_module_level=True)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.vllm,
    pytest.mark.gpu_1,
    pytest.mark.pre_merge,
    pytest.mark.profiled_vram_gib(0),
]


class _FakeDescriptor:
    def __init__(self, tensor: torch.Tensor):
        self.tensor = tensor


class _FakeMeta:
    def __init__(self, token: str, tensors):
        self._token = token
        self._tensors = tensors

    def model_dump(self):
        return {
            "token": self._token,
            "descriptors": [{"size": t.nbytes} for t in self._tensors],
        }


class _FakeReadableOp:
    def __init__(self, token: str, registry: dict[str, dict]):
        self._token = token
        self._registry = registry

    def metadata(self):
        return _FakeMeta(self._token, self._registry[self._token]["src"])

    async def wait_for_completion(self):
        await self._registry[self._token]["read_done"].wait()


class _FakeReadOp:
    async def wait_for_completion(self):
        return None


class _FakeConnector:
    def __init__(self):
        self._registry: dict[str, dict] = {}
        self._counter = 0

    async def create_readable(self, descriptors):
        desc_list = descriptors if isinstance(descriptors, list) else [descriptors]
        self._counter += 1
        token = f"tok-{self._counter}"
        self._registry[token] = {
            "src": [d.tensor for d in desc_list],
            "read_done": nixl_module.asyncio.Event(),
        }
        return _FakeReadableOp(token, self._registry)

    async def begin_read(self, rdma_meta, local_descriptors):
        token = rdma_meta["token"]
        local_list = (
            local_descriptors
            if isinstance(local_descriptors, list)
            else [local_descriptors]
        )
        src_list = self._registry[token]["src"]
        for src, dst in zip(src_list, [d.tensor for d in local_list], strict=False):
            dst.copy_(src)
        self._registry[token]["read_done"].set()
        return _FakeReadOp()


class _FakeRdmaMetadata(dict):
    @property
    def descriptors(self):
        return [SimpleNamespace(**entry) for entry in self["descriptors"]]

    @classmethod
    def model_validate(cls, value):
        return cls(value)


@pytest.fixture
def fake_nixl(monkeypatch):
    fake_mod = SimpleNamespace(
        Connector=_FakeConnector,
        Descriptor=_FakeDescriptor,
        RdmaMetadata=_FakeRdmaMetadata,
    )
    monkeypatch.setattr(nixl_module, "_nixl_connect", fake_mod)
    monkeypatch.setattr(nixl_module, "_NIXL_AVAILABLE", True)
    return fake_mod


def test_nixl_connector_tensor_roundtrip(fake_nixl):
    connector = nixl_module.DynamoOmniNixlConnector(config={})
    try:
        payload = torch.arange(8, dtype=torch.float32)
        ok, size, metadata = connector.put("0", "1", "req-tensor", payload)

        assert ok is True
        assert metadata is not None
        assert size == payload.numel() * payload.element_size()
        assert metadata["kind"] == "tensor_list"

        out = connector.get("0", "1", "req-tensor", metadata=metadata)
        assert out is not None
        received, rx_size = out
        assert rx_size == size
        assert isinstance(received, torch.Tensor)
        assert torch.equal(received, payload)
    finally:
        connector.close()


def test_nixl_connector_object_roundtrip(fake_nixl):
    connector = nixl_module.DynamoOmniNixlConnector(config={})
    try:
        payload = {
            "kind": "text",
            "outputs": [{"token_ids": [1, 2], "text": "hi", "finish_reason": "stop"}],
            "multimodal_output": {"sr": 16000},
        }
        ok, size, metadata = connector.put("1", "router", "req-obj", payload)

        assert ok is True
        assert metadata is not None
        assert size > 0
        assert metadata["kind"] == "serialized_obj"

        out = connector.get("1", "router", "req-obj", metadata=metadata)
        assert out is not None
        received, _ = out
        assert received == payload
    finally:
        connector.close()


def test_nixl_connector_cleanup_retains_published_lease(fake_nixl):
    connector = nixl_module.DynamoOmniNixlConnector(config={"pending_timeout_s": 60})
    try:
        ok, _, metadata = connector.put(
            "0", "1", "req-clean", torch.tensor([1], dtype=torch.int64)
        )
        assert ok is True
        assert "req-clean" in connector._pending

        connector.cleanup("req-clean")
        assert "req-clean" not in connector._pending
        assert len(connector._retired) == 1
        with pytest.raises(RuntimeError, match="outstanding DMA"):
            connector.close()
        # A late reader still owns a valid descriptor after cleanup.
        assert connector.get("0", "1", "req-clean", metadata=metadata) is not None
        assert not connector._retired
    finally:
        connector.close()


def test_register_dynamoomni_nixl_connector_registers_when_missing(monkeypatch):
    factory = MagicMock()
    factory.list_registered_connectors.return_value = []
    monkeypatch.setattr(nixl_module, "OmniConnectorFactory", factory)
    monkeypatch.setattr(nixl_module, "_OMNI_FACTORY_IMPORT_ERROR", None)

    nixl_module.register_dynamoomni_nixl_connector()

    factory.register_connector.assert_called_once_with(
        "NixlConnector",
        nixl_module.create_dynamoomni_nixl_connector,
    )


@pytest.mark.asyncio
async def test_publish_budget_counts_in_progress_registration(fake_nixl):
    connector = nixl_module.DynamoOmniNixlConnector(config={"max_pending_bytes": 16})
    original = connector._connector.create_readable

    async def exercise():
        entered = nixl_module.asyncio.Event()
        release = nixl_module.asyncio.Event()

        async def slow_create(descriptors):
            entered.set()
            await release.wait()
            return await original(descriptors)

        connector._connector.create_readable = slow_create
        first = nixl_module.asyncio.create_task(
            connector._put_async("0", "1", "first", torch.ones(4))
        )
        await entered.wait()
        second = await connector._put_async("0", "1", "second", torch.ones(4))
        assert second == (False, 0, None)
        with pytest.raises(RuntimeError, match="outstanding DMA"):
            await connector._close_async()
        release.set()
        ok, _, meta = await first
        assert ok
        assert await connector._get_async("0", "1", "first", meta) is not None

    try:
        await nixl_module.asyncio.to_thread(connector._run_in_loop, exercise())
    finally:
        connector.close()
