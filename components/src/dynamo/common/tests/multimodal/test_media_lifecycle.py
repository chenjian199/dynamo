# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import ctypes
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
import torch

from dynamo.common.multimodal import embedding_transfer as transfer
from dynamo.common.multimodal.local_store import LocalMediaStore

pytestmark = [
    pytest.mark.unit,
    pytest.mark.gpu_0,
    pytest.mark.pre_merge,
    pytest.mark.timeout(30),
]


def test_expired_collection_respects_reader(tmp_path):
    path = tmp_path / "encoder_cache.test.safetensors"
    path.write_bytes(b"payload")
    store = LocalMediaStore(str(tmp_path), ttl=10)
    with store.acquire(str(path)):
        os.utime(path, (0, 0))
        assert store.collect() == 0
        assert path.exists()
    assert store.collect() == 1
    assert not path.exists()


def test_expired_and_foreign_files(tmp_path):
    path = tmp_path / "encoder_cache.test.safetensors"
    path.write_bytes(b"payload")
    os.utime(path, (0, 0))
    other = tmp_path / "user-file.safetensors"
    other.write_bytes(b"keep")
    store = LocalMediaStore(str(tmp_path), ttl=10)
    with pytest.raises(TimeoutError):
        store.acquire(str(path))
    with pytest.raises(ValueError):
        store.acquire(str(other))
    assert store.collect() == 1
    assert other.exists()


def test_local_store_ignores_non_regular_entries(tmp_path):
    fifo = tmp_path / "encoder_cache.pipe.safetensors"
    os.mkfifo(fifo)
    link = tmp_path / "encoder_cache.link.safetensors"
    link.symlink_to(fifo)
    store = LocalMediaStore(str(tmp_path), ttl=1)
    assert store.collect() == 0
    with pytest.raises(ValueError, match="regular file"):
        store.acquire(str(fifo))
    assert fifo.exists()


@pytest.mark.asyncio
async def test_local_release_preserves_another_receivers_unopened_mapping(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    sender, receiver = (
        transfer.LocalEmbeddingSender(),
        transfer.LocalEmbeddingReceiver(),
    )
    request, complete = await sender.send_embeddings(torch.ones(4))
    await complete
    tensor_id, _ = await receiver.receive_embeddings(request)
    path = request.serialized_request
    store = LocalMediaStore(str(tmp_path), ttl=10)
    with store.acquire(path):
        receiver.release_tensor(tensor_id)
        assert os.path.exists(path)
        os.utime(path, (0, 0))
        assert store.collect() == 0
    assert store.collect() == 1


class Agent:
    agents = {}
    state = "DONE"

    def __init__(self, name, config):
        self.name = name
        self.notifications = {}
        self.registrations = []
        self.handles = []
        self.agents[name] = self

    def get_agent_metadata(self):
        return self.name.encode()

    def add_remote_agent(self, metadata):
        return metadata.decode()

    def register_memory(self, tensor):
        self.registrations.append(tensor)
        return tensor

    def deregister_memory(self, tensor):
        self.registrations = [item for item in self.registrations if item is not tensor]

    def get_xfer_descs(self, tensor, **kwargs):
        return tensor

    def initialize_xfer(self, mode, source, target, peer):
        handle = SimpleNamespace(source=source, target=target, released=False)
        self.handles.append(handle)
        return handle

    def transfer(self, handle):
        return "PROC"

    def check_xfer_state(self, handle):
        if self.state == "DONE":
            address, size, _ = handle.target[0]
            ctypes.memmove(address, handle.source.data_ptr(), size)
        return self.state

    def release_xfer_handle(self, handle):
        assert self.state in ("DONE", "ERR")
        handle.released = True

    def send_notif(self, peer, notif_msg):
        self.agents[peer].notifications.setdefault(self.name, []).append(notif_msg)

    def get_new_notifs(self):
        result, self.notifications = self.notifications, {}
        return result


@pytest.fixture
def agents(monkeypatch):
    monkeypatch.setattr(Agent, "agents", {})
    monkeypatch.setattr(Agent, "state", "DONE")
    monkeypatch.setattr(transfer, "_load_nixl_api", lambda: (Agent, lambda **kw: None))
    monkeypatch.setenv("DYN_PLATFORM", "cpu")


async def settle(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_write_success_and_alignment(agents):
    sender = transfer.NixlWriteEmbeddingSender()
    receiver = transfer.NixlWriteEmbeddingReceiver(buffer_size=128)
    try:
        for tensor in (
            torch.arange(3, dtype=torch.uint8),
            torch.arange(4, dtype=torch.float64),
        ):
            request, completion = await sender.send_embeddings(tensor)
            tensor_id, actual = await receiver.receive_embeddings(request)
            assert torch.equal(actual, tensor)
            receiver.release_tensor(tensor_id)
            await completion
        assert sender.pending_bytes == 0
        assert all(handle.released for handle in sender.nixl_agent.handles)
    finally:
        await sender.close()
        await receiver.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_late_dma_cannot_reuse_destination(agents, monkeypatch, cancel):
    monkeypatch.setattr(Agent, "state", "PROC")
    sender = transfer.NixlWriteEmbeddingSender()
    receiver = transfer.NixlWriteEmbeddingReceiver(buffer_size=16)
    # Use int32 so exactly one request fits in the ring.
    request, completion = await sender.send_embeddings(
        torch.arange(4, dtype=torch.int32)
    )
    receive = asyncio.create_task(
        receiver.receive_embeddings(request, receive_timeout=0.05)
    )
    await settle(lambda: bool(sender.nixl_agent.handles))
    if cancel:
        receive.cancel()
        with pytest.raises(asyncio.CancelledError):
            await receive
    else:
        with pytest.raises(TimeoutError):
            await receive
    assert receiver.ring_buffer.get_buffer(16) == (None, None)
    assert sender.nixl_agent.registrations
    monkeypatch.setattr(Agent, "state", "DONE")
    await completion
    await settle(lambda: not receiver.pending)
    assert not receiver.ring_buffer.allocated_buffer_id_to_range
    await sender.close()
    await receiver.close()


@pytest.mark.asyncio
async def test_error_is_not_success(agents, monkeypatch):
    monkeypatch.setattr(Agent, "state", "ERR")
    sender = transfer.NixlWriteEmbeddingSender()
    receiver = transfer.NixlWriteEmbeddingReceiver(buffer_size=64)
    request, completion = await sender.send_embeddings(torch.ones(4))
    with pytest.raises(RuntimeError, match="failed"):
        await receiver.receive_embeddings(request)
    with pytest.raises(RuntimeError, match="failed"):
        await completion
    assert not receiver.ring_buffer.allocated_buffer_id_to_range
    await sender.close()
    await receiver.close()


@pytest.mark.asyncio
async def test_unclaimed_source_expires_and_late_handshake_rejected(agents):
    sender = transfer.NixlWriteEmbeddingSender(transfer_timeout=0.01)
    receiver = transfer.NixlWriteEmbeddingReceiver(buffer_size=64)
    request, completion = await sender.send_embeddings(torch.ones(4))
    with pytest.raises(TimeoutError):
        await completion
    with pytest.raises(RuntimeError):
        await receiver.receive_embeddings(request)
    assert not sender.nixl_agent.handles
    assert not receiver.pending
    await sender.close()
    await receiver.close()


def test_dead_writer_partial_is_reclaimed(tmp_path):
    path = tmp_path / "encoder_cache.dead.partial"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,fcntl,sys; f=open(sys.argv[1], 'wb'); "
            "fcntl.flock(f, fcntl.LOCK_EX); f.write(b'partial'); f.flush(); os._exit(9)",
            str(path),
        ],
        timeout=10,
        check=False,
    )
    os.utime(path, (0, 0))
    assert LocalMediaStore(str(tmp_path), ttl=10).collect() == 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_cancelled_decode_keeps_concurrency_slot(monkeypatch):
    from dynamo.common.multimodal.video_loader import _bounded_decode

    monkeypatch.setenv("DYN_VIDEO_DECODE_CONCURRENCY", "1")
    started, finish, second_started = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def blocked():
        started.set()
        if not finish.wait(timeout=5):
            raise TimeoutError("test did not release decoder")

    first = asyncio.create_task(_bounded_decode(blocked))
    try:
        await settle(started.is_set)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(_bounded_decode(second_started.set))
        await asyncio.sleep(0.02)
        assert not second_started.is_set()
        finish.set()
        await asyncio.wait_for(second, 2)
        assert second_started.is_set()
    finally:
        finish.set()
