# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Malformed layouts and asynchronous ownership on vendor-neutral media paths."""

import asyncio
import os
import threading
from queue import Queue
from types import SimpleNamespace

import pytest
import torch

from dynamo.common.multimodal import embedding_transfer as et
from dynamo.common.multimodal import nvdec_decoder
from dynamo.common.utils.media_nixl import validate_media_descriptor

pytestmark = [pytest.mark.pre_merge, pytest.mark.unit, pytest.mark.gpu_0]


@pytest.mark.parametrize(
    ("shape", "dtype"),
    [
        ([1, -1], "float32"),
        ([True, 2], "float32"),
        ([2**40], "float32"),
        ([1], "float33"),
        ([1], "load"),
    ],
)
def test_invalid_embedding_metadata(shape, dtype):
    with pytest.raises(ValueError):
        et.TransferRequest(
            embeddings_shape=shape, embedding_dtype_str=dtype, serialized_request=""
        )


@pytest.mark.asyncio
async def test_local_noncontiguous_concurrent_consumers(tmp_path, monkeypatch):
    monkeypatch.setattr(et.tempfile, "tempdir", str(tmp_path))
    source = torch.arange(12, dtype=torch.float32).reshape(3, 4).T
    sender, receiver = et.LocalEmbeddingSender(), et.LocalEmbeddingReceiver()
    request, done = await sender.send_embeddings(source)
    await done
    first, second = await asyncio.gather(
        receiver.receive_embeddings(request),
        receiver.receive_embeddings(request),
    )
    assert torch.equal(first[1], source) and torch.equal(second[1], source)
    receiver.release_tensor(first[0])
    assert os.path.exists(request.serialized_request)
    receiver.release_tensor(second[0])
    receiver.release_tensor(second[0])
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_local_metadata_mismatch_releases_file(tmp_path, monkeypatch):
    monkeypatch.setattr(et.tempfile, "tempdir", str(tmp_path))
    request, _ = await et.LocalEmbeddingSender().send_embeddings(torch.ones(2, 3))
    request.embeddings_shape = [3, 2]
    with pytest.raises(ValueError, match="shape"):
        await et.LocalEmbeddingReceiver().receive_embeddings(request)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize("cancel_sender", [False, True])
async def test_cancelled_local_io_drains_before_unlink(
    tmp_path, monkeypatch, cancel_sender
):
    monkeypatch.setattr(et.tempfile, "tempdir", str(tmp_path))
    entered, finish = threading.Event(), threading.Event()
    sender, receiver = et.LocalEmbeddingSender(), et.LocalEmbeddingReceiver()
    if cancel_sender:
        original = sender.save_embeddings_to_file

        def blocked(*args):
            entered.set()
            assert finish.wait(5)
            return original(*args)

        monkeypatch.setattr(sender, "save_embeddings_to_file", blocked)
        task = asyncio.create_task(sender.send_embeddings(torch.ones(2, 3)))
    else:
        request, _ = await sender.send_embeddings(torch.ones(2, 3))
        original = et.safetensors_torch.load_file

        def blocked(*args):
            entered.set()
            assert finish.wait(5)
            assert os.path.exists(request.serialized_request)
            return original(*args)

        monkeypatch.setattr(et.safetensors_torch, "load_file", blocked)
        task = asyncio.create_task(receiver.receive_embeddings(request))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        finish.set()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not list(tmp_path.iterdir()) and not receiver.received_tensors:
            # Sender may not have written its file yet; drain its callback too.
            if not cancel_sender:
                break
    assert not list(tmp_path.iterdir())
    assert not receiver.received_tensors


@pytest.mark.parametrize("platform", ["metax", "ascend", "cpu"])
def test_vendor_platform_cannot_load_nvdec(monkeypatch, platform):
    monkeypatch.setenv("DYN_PLATFORM", platform)
    nvdec_decoder.nvdec_available.cache_clear()
    assert not nvdec_decoder.nvdec_available()
    with pytest.raises(RuntimeError, match="CUDA platform"):
        nvdec_decoder.decode_video_nvdec(b"", 1)
    nvdec_decoder.nvdec_available.cache_clear()


def _media():
    return {
        "shape": [2, 3, 3],
        "dtype": "uint8",
        "nixl_descriptor": {"mem_type": "dram", "addr": 4096, "size": 18},
    }


def test_media_rejects_unknown_space_and_size(monkeypatch):
    monkeypatch.setenv("DYN_PLATFORM", "ascend")
    metadata = _media()
    assert validate_media_descriptor(metadata)[0] == "cpu"
    metadata["nixl_descriptor"]["mem_type"] = "npu"
    with pytest.raises(ValueError, match="stage vendor media"):
        validate_media_descriptor(metadata)
    metadata = _media()
    metadata["nixl_descriptor"]["size"] = 17
    with pytest.raises(ValueError, match="byte size"):
        validate_media_descriptor(metadata)


class _Descriptor:
    def __init__(self, tensor):
        self._data_ref = tensor
        self._data_size = tensor.nbytes


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_cancelled_nixl_read_keeps_destination_until_completion(monkeypatch):
    monkeypatch.setenv("DYN_PLATFORM", "ascend")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def wait():
        entered.set()
        await finish.wait()

    async def begin(*args):
        return SimpleNamespace(wait_for_completion=wait)

    receiver = et.NixlReadEmbeddingReceiver.__new__(et.NixlReadEmbeddingReceiver)
    receiver._nixl_connect = SimpleNamespace(
        Descriptor=_Descriptor,
        RdmaMetadata=SimpleNamespace(model_validate=lambda value: value),
    )
    receiver.connector = SimpleNamespace(begin_read=begin)
    receiver.warmedup_descriptors = Queue()
    receiver.warmedup_descriptors.put(_Descriptor(torch.zeros(128, dtype=torch.int8)))
    receiver.inuse_descriptors, receiver._pending_reads = {}, set()
    receiver.tensor_id_counter = 0
    request = et.TransferRequest(
        embeddings_shape=[2, 3],
        embedding_dtype_str="float32",
        serialized_request=SimpleNamespace(
            descriptors=[SimpleNamespace(size=24, device="cpu")]
        ),
    )
    task = asyncio.create_task(receiver.receive_embeddings(request))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert receiver.warmedup_descriptors.empty()
    assert len(receiver._pending_reads) == 1
    finish.set()
    await asyncio.gather(*receiver._pending_reads)
    await asyncio.sleep(0)
    assert receiver.warmedup_descriptors.qsize() == 1
    assert receiver.warmedup_descriptors.get()._data_size == 128
