# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Manual, real-device M07 host-staged embedding checks; no downloaded models."""

import asyncio
import base64
import os
from pathlib import Path

import pytest
import torch

from dynamo.common.multimodal.embedding_transfer import (
    LocalEmbeddingReceiver,
    LocalEmbeddingSender,
    NixlReadEmbeddingReceiver,
    NixlReadEmbeddingSender,
    NixlWriteEmbeddingReceiver,
    NixlWriteEmbeddingSender,
)

pytestmark = [pytest.mark.post_merge, pytest.mark.integration, pytest.mark.gpu_1]


@pytest.mark.asyncio
@pytest.mark.timeout(90)
@pytest.mark.parametrize("transport", ["local", "nixl_read", "nixl_write"])
@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
async def test_device_embedding_roundtrip(transport, dtype, tmp_path, monkeypatch):
    platform = os.environ["DYN_PLATFORM"]
    ordinal = int(os.environ.get("M07_DEVICE", "0"))
    if platform == "ascend":
        import torch_npu  # noqa: F401 -- registers the NPU torch backend

        device = f"npu:{ordinal}"
        torch.npu.set_device(ordinal)
    else:
        device = f"cuda:{ordinal}"
        torch.cuda.set_device(ordinal)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    if transport == "local":
        sender, receiver = LocalEmbeddingSender(), LocalEmbeddingReceiver()
    elif transport == "nixl_write":
        sender = NixlWriteEmbeddingSender()
        receiver = NixlWriteEmbeddingReceiver(buffer_size=64 * 1024)
    else:
        sender = NixlReadEmbeddingSender()
        receiver = NixlReadEmbeddingReceiver(
            embedding_hidden_size=64,
            max_item_mm_token=16,
            max_items=1,
        )
    # Noncontiguous device tensors and transfers larger than a warm buffer.
    source = torch.arange(2048, dtype=torch.float32).reshape(32, 64).to(device, dtype).T
    for _ in range(3):
        request, completion = await sender.send_embeddings(source)
        try:
            tensor_id, received = await receiver.receive_embeddings(request)
            try:
                assert received.device.type == "cpu"
                assert received.dtype == dtype
                assert torch.equal(received.to(device), source)
            finally:
                receiver.release_tensor(tensor_id)
            await asyncio.wait_for(completion, timeout=30)
        except BaseException:
            # Consume the awaitable on failed tests too.
            task = asyncio.ensure_future(completion)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    if transport == "nixl_write":
        await sender.close()
        await receiver.close()
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_cpu_video_decode(tmp_path):
    from dynamo.common.multimodal.video_loader import VideoLoader

    video = Path(__file__).with_name("red.webm")
    uri = "data:video/webm;base64," + base64.b64encode(video.read_bytes()).decode()
    loader = VideoLoader(num_frames=4)
    for _ in range(5):
        frames, metadata = await loader.load_video(uri)
        assert frames.shape == (4, 64, 64, 3)
        assert frames.flags.c_contiguous
        assert frames[..., 0].mean() > 200
        assert frames[..., 1:].mean() < 20
        assert metadata["fps"] > 0


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_write_late_submission_after_receiver_timeout():
    """Real NIXL WRITE after timeout must target quarantined, unreused memory."""
    sender = NixlWriteEmbeddingSender()
    receiver = NixlWriteEmbeddingReceiver(buffer_size=64)
    source = torch.arange(16, dtype=torch.float32)
    request, completion = await sender.send_embeddings(source)
    # Delay the real submission until the receiving request has timed out.
    sender._state_update_task.cancel()
    await asyncio.gather(sender._state_update_task, return_exceptions=True)
    with pytest.raises(TimeoutError):
        await receiver.receive_embeddings(request, receive_timeout=0.05)
    assert receiver.ring_buffer.get_buffer(64) == (None, None)
    sender._state_update_task = asyncio.create_task(sender._state_update())
    await asyncio.wait_for(completion, 30)
    async with asyncio.timeout(30):
        while receiver.pending:
            await asyncio.sleep(0.005)
    request, completion = await sender.send_embeddings(source + 7)
    tensor_id, received = await receiver.receive_embeddings(request)
    assert torch.equal(received, source + 7)
    receiver.release_tensor(tensor_id)
    await completion
    await sender.close()
    await receiver.close()
