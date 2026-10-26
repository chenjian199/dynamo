# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Derived from NVIDIA Dynamo. Modified by HEYI.

import asyncio
import base64
import contextlib
import fcntl
import logging
import math
import os
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from queue import Queue
from typing import Any, Awaitable, List, Literal

import msgspec
import torch
from pydantic import BaseModel, StrictInt, model_validator
from safetensors import torch as safetensors_torch

from dynamo.common.multimodal.local_store import local_media_store
from dynamo.common.multimodal.tensor_contract import (
    selected_platform,
    tensor_dtype,
    tensor_nbytes,
    transfer_tensor,
)
from dynamo.common.utils import nvtx_utils as _nvtx
from dynamo.common.utils.runtime import run_async

logger = logging.getLogger(__name__)


def _load_nixl_api():
    try:
        from nixl._api import nixl_agent, nixl_agent_config
    except ImportError as exc:
        raise RuntimeError(
            "NIXL is required for NIXL embedding transfer; install nixl "
            "to use NIXL write-based multimodal embedding transfer."
        ) from exc

    return nixl_agent, nixl_agent_config


def _load_nixl_connect():
    try:
        import dynamo.nixl_connect as nixl_connect
    except ImportError as exc:
        raise RuntimeError(
            "NIXL is required for NIXL embedding transfer; install "
            "dynamo.nixl_connect to use NIXL read-based transfers."
        ) from exc

    return nixl_connect


def torch_dtype_from_string(dtype_str: str) -> torch.dtype:
    """Convert dtype string to torch.dtype object.

    Args:
        dtype_str: String representation of torch dtype (e.g., "torch.float32")

    Returns:
        Corresponding torch.dtype object

    Example:
        >>> dtype = EncodeHelper.get_torch_dtype_from_string("torch.bfloat16")
        >>> # Result: torch.bfloat16
    """
    return tensor_dtype(dtype_str)


def torch_dtype_to_string(dtype: torch.dtype) -> str:
    return str(dtype).removeprefix("torch.")


# Opaque object to the caller, different implementation may carry
# different information (e.g. local file path vs nixl metadata)
class TransferRequest(BaseModel):
    """
    Data class for transfer requests containing necessary information for embedding transfer.
    """

    embeddings_shape: List[StrictInt]
    embedding_dtype_str: str
    serialized_request: Any
    schema_version: Literal[1] = 1
    source_platform: Literal["cpu", "cuda", "metax", "ascend", "xpu"] | None = None

    @model_validator(mode="after")
    def validate_tensor(self):
        tensor_nbytes(self.embeddings_shape, self.embedding_dtype_str)
        return self


class AbstractEmbeddingReceiver(ABC):
    """
    Abstract base class for a receiver of precomputed embeddings from the encode worker.
    """

    @abstractmethod
    async def receive_embeddings(
        self, request: TransferRequest
    ) -> tuple[int, torch.Tensor]:
        """
        Abstract method to receive precomputed embeddings for a given request ID.

        Args:
            request: The TransferRequest object containing information to receive embeddings.

        Returns:
            A tuple containing the tensor ID and the received embeddings as a torch.Tensor.
            Caller should invoke release_tensor(tensor_id) when the tensor is no longer needed to free up resources.
        """
        pass

    @abstractmethod
    def release_tensor(self, tensor_id: int) -> None:
        """
        Abstract method to indicate that the tensor associated with the ID is no longer in use.
        Args:
            tensor_id: The ID of the tensor to release.
        """
        pass


class AbstractEmbeddingSender(ABC):
    """
    Abstract base class for a sender of precomputed embeddings to the downstream worker.
    """

    @abstractmethod
    async def send_embeddings(
        self, embeddings: torch.Tensor, stage_embeddings: bool = False
    ) -> tuple[TransferRequest, Awaitable[None]]:
        """
        Abstract method to send precomputed embeddings for a given request ID.

        Args:
            embeddings: A torch.Tensor of the embeddings to send.
            stage_embeddings: A boolean indicating whether the embeddings should be staged for the transfer,
            if True, the embeddings may be used as transfer buffer and must not be released until the return future is completed.
        Returns:
            A tuple containing the TransferRequest object and an awaitable that can be awaited to indicate the send is completed.
        """
        pass


class LocalEmbeddingSender(AbstractEmbeddingSender):
    """
    Sender that saves embeddings to a local file and sends the file path as the serialized request.
    """

    def __init__(self):
        self.store = local_media_store()
        self.sender_id = uuid.uuid4().hex
        self.embedding_counter = 0

    def save_embeddings_to_file(
        self, embedding_key: str, embeddings: torch.Tensor
    ) -> str:
        """
        Save the embeddings to a local file and return the file path.

        Args:
            embedding_key: A unique key for the embeddings.
            embeddings: A torch.Tensor of the embeddings to save.
        Returns:
            The file path where the embeddings are saved.
        """
        fd, tensor_path = tempfile.mkstemp(
            prefix=f"encoder_cache.{embedding_key}.", suffix=".partial"
        )
        try:
            with os.fdopen(fd, "wb") as lease:
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX)
                tensors = {"ec_cache": embeddings.detach().cpu().contiguous()}
                safetensors_torch.save_file(tensors, tensor_path)
                os.utime(tensor_path, None)
                published = tensor_path.removesuffix(".partial") + ".safetensors"
                os.rename(tensor_path, published)
                tensor_path = published
        except BaseException:
            os.unlink(tensor_path)
            raise
        return tensor_path

    @_nvtx.annotate("mm:local:send_embeddings", color="magenta")
    async def send_embeddings(
        self, embeddings: torch.Tensor, stage_embeddings: bool = False
    ) -> tuple[TransferRequest, Awaitable[None]]:
        """
        Send precomputed embeddings for a given request ID.

        Args:
            embeddings: A torch.Tensor of the embeddings to send.
            stage_embeddings: A boolean indicating whether the embeddings should be staged for the transfer,
            if True, the embeddings may be used as transfer buffer and must not be released until the return future is completed.
        Returns:
            A tuple containing the TransferRequest object and an awaitable that can be awaited to indicate the send is completed.
        """
        # Implementation to send embeddings to the downstream worker
        # This could involve publishing to a message queue or making an API call
        embedding_key = f"{self.sender_id}_{self.embedding_counter}"
        self.embedding_counter += 1
        source_platform = selected_platform()
        tensor_nbytes(list(embeddings.shape), str(embeddings.dtype))
        save = asyncio.create_task(
            asyncio.to_thread(self.save_embeddings_to_file, embedding_key, embeddings)
        )
        try:
            tensor_path = await asyncio.shield(save)
        except asyncio.CancelledError:
            # The copy thread still owns the tensor. Delete only after it finishes.
            def discard(task):
                if not task.cancelled() and task.exception() is None:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(task.result())

            save.add_done_callback(discard)
            raise
        fut = asyncio.get_event_loop().create_future()
        fut.set_result(None)
        return (
            TransferRequest(
                embeddings_shape=list(embeddings.shape),
                embedding_dtype_str=torch_dtype_to_string(embeddings.dtype),
                serialized_request=tensor_path,
                source_platform=source_platform,
            ),
            fut,
        )


class LocalEmbeddingReceiver(AbstractEmbeddingReceiver):
    """
    Receiver that reads embeddings from a local file path provided in the serialized request.
    """

    def __init__(self):
        super().__init__()
        self.received_tensors = {}
        self.tensor_id_counter = 0
        self._path_refs = {}
        self._leases = {}
        self.store = local_media_store()

    @_nvtx.annotate("mm:local:receive_embeddings", color="magenta")
    async def receive_embeddings(
        self, request: TransferRequest
    ) -> tuple[int, torch.Tensor]:
        """
        Receive precomputed embeddings for a given request ID.

        Args:
            request: The TransferRequest object containing information to receive embeddings for.

        Returns:
            A tuple containing the tensor ID and the received embeddings as a torch.Tensor.
            Caller should invoke release_tensor(tensor_id) when the tensor is no longer needed to free up resources.
        """
        tensor_path = request.serialized_request
        tensor_id = self.tensor_id_counter
        self.tensor_id_counter += 1
        self._leases[tensor_id] = self.store.acquire(tensor_path)
        self.received_tensors[tensor_id] = tensor_path
        self._path_refs[tensor_path] = self._path_refs.get(tensor_path, 0) + 1
        load = asyncio.create_task(
            asyncio.to_thread(safetensors_torch.load_file, tensor_path)
        )
        try:
            tensors = await asyncio.shield(load)
            embedding_tensor = tensors["ec_cache"]
            if list(embedding_tensor.shape) != request.embeddings_shape:
                raise ValueError("embedding shape does not match transfer metadata")
            if embedding_tensor.dtype != tensor_dtype(request.embedding_dtype_str):
                raise ValueError("embedding dtype does not match transfer metadata")
            return tensor_id, embedding_tensor
        except asyncio.CancelledError:

            def discard(task):
                if not task.cancelled():
                    task.exception()
                self.release_tensor(tensor_id)

            load.add_done_callback(discard)
            raise
        except BaseException:
            self.release_tensor(tensor_id)
            raise

    def release_tensor(self, tensor_id: int) -> None:
        """
        Indicate that the tensor associated with the ID is no longer in use.

        Args:
            tensor_id: The ID of the tensor to release.
        """
        if tensor_id in self.received_tensors:
            lease = self._leases.pop(tensor_id)
            file_path = self.received_tensors.pop(tensor_id)
            self._path_refs[file_path] -= 1
            try:
                if self._path_refs[file_path] == 0:
                    del self._path_refs[file_path]
                    # Another process may have acquired its read lease but not
                    # opened the safetensors mapping yet. Leave that file to GC.
                    fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    os.remove(file_path)
            except (FileNotFoundError, BlockingIOError):
                pass
            finally:
                lease.close()


class MonolithicCounter:
    """
    A simple counter implementation for generating unique IDs.
    """

    def __init__(self):
        self.counter = 0

    def get_next_id(self) -> int:
        current_id = self.counter
        self.counter += 1
        return current_id


class RingBuffer:
    """
    A ring buffer implementation for managing memory allocation.
    Uses a circular buffer pattern to efficiently reuse memory without wrapped-around allocations.
    When insufficient space remains at the end, allocation restarts from the beginning.
    """

    BufferId = int

    def __init__(self, buffer_size):
        self.buffer_tensor = torch.zeros(buffer_size, dtype=torch.int8)
        # Index tracking for the ring buffer, when
        # free_start_idx < allocated_start_idx, the allocation has been wrapped around,
        # so the allocation request should be rejected if the requested size is larger
        # than the remaining space before allocated_start_idx.
        self.free_start_idx = 0
        self.allocated_start_idx = 0
        self.buffer_size = buffer_size
        self.end_idx = buffer_size
        self.wrapped_around = False

        # Track allocated buffers and their release state,
        # keeping released range in 'freed_list' for simpler monotonical buffer release
        self.freed_list = {}
        self.allocated_buffer_id_to_range = {}
        # For generate buffer IDs
        self.id_counter = MonolithicCounter()

    def __repr__(self):
        return f"RingBuffer(size={self.buffer_size}, free_start_idx={self.free_start_idx}, allocated_start_idx={self.allocated_start_idx}, wrapped_around={self.wrapped_around}, freed_list={self.freed_list}, allocated_buffers={self.allocated_buffer_id_to_range})"

    def _flush_freed_list(self):
        allocated_end = self.freed_list.pop(self.allocated_start_idx, None)
        while allocated_end is not None:
            self.allocated_start_idx = allocated_end
            if self.allocated_start_idx == self.end_idx:
                self.allocated_start_idx = 0
                self.wrapped_around = False
            allocated_end = self.freed_list.pop(self.allocated_start_idx, None)
        # No allocated buffer, reset indices. Important as the ring buffer doesn't
        # support non-contiguous allocation, this make sure the next allocation can
        # use the full buffer.
        if not self.allocated_buffer_id_to_range:
            self.free_start_idx = 0
            self.allocated_start_idx = 0
            self.wrapped_around = False

    def get_buffer(self, size):
        """
        Get a buffer of given size in the form of 1D tensor with dtype int8,
        the buffer is owned by the RingBuffer instance.
        The returned ID will be used for releasing the buffer after use, as
        an indicator that the buffer can be reused for future allocation.

        Args:
            size: The size of the buffer to allocate.

        Returns:
            A tuple containing the buffer ID and the allocated tensor, or None if allocation fails.
        """
        # [gluo TODO] raise exception as there is no way to satisfy the request.
        # Can not allocate for sure
        if size > self.buffer_size:
            return None, None
        # Sanity clean up freed list
        self._flush_freed_list()

        # If the allocation will go over end boundary, simply try allocate from the start
        if self.free_start_idx + size > self.end_idx:
            # Not enough space even after wrap around, reject the allocation early
            # so we don't mark the remaining space "used"
            if self.allocated_start_idx < size:
                return None, None
            # add artificial entry to freed_list to treat the remaining space to be
            # allocated and released.
            self.freed_list[self.free_start_idx] = self.end_idx
            self.free_start_idx = 0
            self.wrapped_around = True
        start_idx = self.free_start_idx
        end_idx = start_idx + size

        # Check availability of the buffer, if the allocation overlaps with allocated buffer,
        # return None for the caller to retry later after some buffers are released.
        if self.wrapped_around and end_idx > self.allocated_start_idx:
            return None, None

        # book-keep allocations
        buffer_id = self.id_counter.get_next_id()
        self.allocated_buffer_id_to_range[buffer_id] = (start_idx, end_idx)
        self.free_start_idx = end_idx

        return buffer_id, self.buffer_tensor[start_idx:end_idx]

    def release_buffer(self, buffer_id):
        start_end = self.allocated_buffer_id_to_range.pop(buffer_id, None)
        if start_end is not None:
            self.freed_list[start_end[0]] = start_end[1]
            self._flush_freed_list()


class NixlTransferRequest(BaseModel):
    protocol_version: Literal[2]
    sender_agent_id: str
    agent_metadata: str
    tensor_id: int
    tensor_size: int


class NixlWriteEmbeddingSender(AbstractEmbeddingSender):
    """WRITE v2: acknowledge only terminal, released transfers.

    A deadline fails the caller, but cannot authorize deregistration of an
    in-flight source. Such transfers remain charged to a bounded memory budget.
    """

    def __init__(self, transfer_timeout=60, max_pending_bytes=8 * 1024**3):
        if (
            not math.isfinite(transfer_timeout)
            or transfer_timeout <= 0
            or max_pending_bytes <= 0
        ):
            raise ValueError(
                "WRITE timeout and memory budget must be positive and finite"
            )
        nixl_agent, nixl_agent_config = _load_nixl_api()
        self.sender_id = f"sender_{uuid.uuid4()}"
        self.nixl_agent = nixl_agent(
            self.sender_id, nixl_agent_config(num_threads=8, capture_telemetry=True)
        )
        self.agent_metadata_b64 = base64.b64encode(
            self.nixl_agent.get_agent_metadata()
        ).decode()
        self.transfer_timeout = transfer_timeout
        self.max_pending_bytes = max_pending_bytes
        self.pending_bytes = 0
        self.remote_agents = {}
        self.transfer_tracker = {}
        self.id_counter = MonolithicCounter()
        self._closed = False
        self._state_update_task = asyncio.create_task(self._state_update())

    def _notify(self, peer, receipt, status):
        self.nixl_agent.send_notif(
            peer, notif_msg=msgspec.msgpack.encode((2, receipt, status))
        )

    def _finish(self, tensor_id, error=None):
        item = self.transfer_tracker[tensor_id]
        # Only called before submission or after a terminal state AND release.
        self.nixl_agent.deregister_memory(item.registration)
        del self.transfer_tracker[tensor_id]
        self.pending_bytes -= item.tensor.nbytes
        if not item.future.done():
            if error is None:
                item.future.set_result(None)
            else:
                item.future.set_exception(error)

    async def _state_update(self):
        try:
            while not self._closed:
                for peer, notifications in self.nixl_agent.get_new_notifs().items():
                    for notification in notifications:
                        message = msgspec.msgpack.decode(notification)
                        if not isinstance(message, dict) or message.get("version") != 2:
                            raise ValueError("unsupported NIXL WRITE handshake")
                        if peer not in self.remote_agents:
                            self.remote_agents[peer] = self.nixl_agent.add_remote_agent(
                                message["metadata"]
                            )
                        tensor_id, receipt = message["tensor_id"], message["receipt"]
                        item = self.transfer_tracker.get(tensor_id)
                        if item is None:
                            # Expired/unpublished request: no WRITE can be submitted.
                            self._notify(peer, receipt, "ERROR")
                            continue
                        if item.peer is not None:
                            # Never submit the same source twice. A different
                            # destination was never authorized, so it can reclaim.
                            if (peer, receipt) != (item.peer, item.receipt):
                                self._notify(peer, receipt, "ERROR")
                            continue
                        if (
                            message["size"] != item.tensor.nbytes
                            or message["memory"] != "cpu"
                        ):
                            self._notify(peer, receipt, "ERROR")
                            self._finish(
                                tensor_id, ValueError("invalid WRITE destination")
                            )
                            continue
                        target = self.nixl_agent.get_xfer_descs(
                            [(message["address"], message["size"], 0)], mem_type="cpu"
                        )
                        item.peer, item.receipt = peer, receipt
                        item.handle = self.nixl_agent.initialize_xfer(
                            "WRITE", item.descriptor, target, peer
                        )
                        # Once submission is attempted, an exception is ambiguous:
                        # keep the handle and backing storage until quiescence.
                        self.nixl_agent.transfer(item.handle)
                now = time.monotonic()
                for tensor_id, item in list(self.transfer_tracker.items()):
                    if item.handle is not None:
                        state = self.nixl_agent.check_xfer_state(item.handle)
                        if state in ("DONE", "ERR"):
                            self.nixl_agent.release_xfer_handle(item.handle)
                            item.handle = None
                            self._notify(item.peer, item.receipt, state)
                            self._finish(
                                tensor_id,
                                (
                                    RuntimeError("NIXL WRITE failed")
                                    if state == "ERR"
                                    else None
                                ),
                            )
                            continue
                    if now >= item.deadline:
                        error = TimeoutError("NIXL WRITE completion deadline exceeded")
                        if item.peer is None:
                            self._finish(tensor_id, error)
                        elif not item.future.done():
                            item.future.set_exception(error)
                await asyncio.sleep(0.001 if self.transfer_tracker else 0.01)
        except Exception as exc:
            # A broken progress engine must fail every waiter, never report success
            # or free buffers whose transfer state is unknown.
            for item in self.transfer_tracker.values():
                if not item.future.done():
                    item.future.set_exception(
                        RuntimeError(f"WRITE progress failed: {exc}")
                    )
            raise

    async def send_embeddings(self, embeddings, stage_embeddings=False):
        if self._closed or self._state_update_task.done():
            raise RuntimeError("NIXL WRITE sender is unavailable")
        embeddings = transfer_tensor(embeddings)
        if self.pending_bytes + embeddings.nbytes > self.max_pending_bytes:
            raise BufferError("NIXL WRITE pending memory budget exhausted")
        # An owned copy isolates the transfer from callers mutating/reusing inputs
        # after cancellation. Each registration has an independent lifetime.
        embeddings = embeddings.clone().detach()
        tensor_id = self.id_counter.get_next_id()
        future = asyncio.get_running_loop().create_future()
        # Retrieving the exception suppresses unobserved-future noise without
        # changing what awaiters receive (some callers only retain the request).
        future.add_done_callback(
            lambda fut: None if fut.cancelled() else fut.exception()
        )
        descriptor = self.nixl_agent.get_xfer_descs(embeddings)
        registration = self.nixl_agent.register_memory(embeddings)
        self.transfer_tracker[tensor_id] = _WriteTransfer(
            embeddings,
            descriptor,
            registration,
            future,
            time.monotonic() + self.transfer_timeout,
        )
        self.pending_bytes += embeddings.nbytes
        return (
            TransferRequest(
                embeddings_shape=list(embeddings.shape),
                embedding_dtype_str=torch_dtype_to_string(embeddings.dtype),
                source_platform=selected_platform(),
                serialized_request=NixlTransferRequest(
                    protocol_version=2,
                    sender_agent_id=self.sender_id,
                    agent_metadata=self.agent_metadata_b64,
                    tensor_id=tensor_id,
                    tensor_size=embeddings.nbytes,
                ).model_dump_json(),
            ),
            future,
        )

    async def close(self):
        if self.transfer_tracker:
            raise RuntimeError("cannot close WRITE sender with outstanding transfers")
        self._closed = True
        await self._state_update_task


@dataclass
class _WriteTransfer:
    tensor: torch.Tensor
    descriptor: Any
    registration: Any
    future: asyncio.Future
    deadline: float
    peer: str | None = None
    receipt: int | None = None
    handle: Any = None


@dataclass
class _WriteDestination:
    buffer_id: int
    peer: str
    future: asyncio.Future
    abandoned: bool = False


class NixlWriteEmbeddingReceiver(AbstractEmbeddingReceiver):
    """Bounded receiver: a timeout quarantines, never reuses, a DMA target."""

    def __init__(self, buffer_size=8 * 1024**3):
        if buffer_size < 8:
            raise ValueError("WRITE receiver capacity must be at least 8 bytes")
        self.ring_buffer = RingBuffer(buffer_size)
        nixl_agent, nixl_agent_config = _load_nixl_api()
        self.receiver_id = f"receiver_{uuid.uuid4()}"
        self.nixl_agent = nixl_agent(
            self.receiver_id, nixl_agent_config(num_threads=8, capture_telemetry=True)
        )
        self.reg_descs = self.nixl_agent.register_memory(self.ring_buffer.buffer_tensor)
        self.agent_metadata = self.nixl_agent.get_agent_metadata()
        self.remote_agents = {}
        self.id_counter = MonolithicCounter()
        self.to_buffer_id = {}
        self.pending = {}
        self._closed = False
        self._progress = asyncio.create_task(self._state_update())

    async def _state_update(self):
        try:
            while not self._closed:
                for peer, notifications in self.nixl_agent.get_new_notifs().items():
                    for notification in notifications:
                        version, receipt, status = msgspec.msgpack.decode(notification)
                        if version != 2 or status not in ("DONE", "ERR", "ERROR"):
                            raise ValueError("unsupported WRITE completion")
                        item = self.pending.get(receipt)
                        if item is None or item.peer != peer:
                            continue
                        del self.pending[receipt]
                        if item.abandoned or status != "DONE":
                            self.ring_buffer.release_buffer(item.buffer_id)
                        else:
                            self.to_buffer_id[receipt] = item.buffer_id
                        if not item.future.done():
                            item.future.set_result(status)
                await asyncio.sleep(0.001 if self.pending else 0.01)
        except Exception as exc:
            for item in self.pending.values():
                if not item.future.done():
                    item.future.set_exception(
                        RuntimeError(f"WRITE progress failed: {exc}")
                    )
            raise

    async def receive_embeddings(self, request, receive_timeout=60):
        if not math.isfinite(receive_timeout) or receive_timeout <= 0:
            raise ValueError("WRITE receive timeout must be finite and positive")
        if self._closed or self._progress.done():
            raise RuntimeError("NIXL WRITE receiver is unavailable")
        remote = NixlTransferRequest.model_validate_json(request.serialized_request)
        size = tensor_nbytes(request.embeddings_shape, request.embedding_dtype_str)
        if remote.tensor_size != size:
            raise ValueError("NIXL size does not match embedding metadata")
        if (size + 7) // 8 * 8 > self.ring_buffer.buffer_size:
            raise BufferError("embedding exceeds WRITE receiver capacity")
        if remote.sender_agent_id not in self.remote_agents:
            self.remote_agents[remote.sender_agent_id] = (
                self.nixl_agent.add_remote_agent(
                    base64.b64decode(remote.agent_metadata)
                )
            )
        deadline = time.monotonic() + receive_timeout
        while True:
            buffer_id, buffer = self.ring_buffer.get_buffer((size + 7) // 8 * 8)
            if buffer is not None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("WRITE buffer exhausted (possibly quarantined)")
            await asyncio.sleep(0.005)
        receipt = self.id_counter.get_next_id()
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(
            lambda fut: None if fut.cancelled() else fut.exception()
        )
        item = _WriteDestination(buffer_id, remote.sender_agent_id, future)
        self.pending[receipt] = item
        try:
            self.nixl_agent.send_notif(
                remote.sender_agent_id,
                notif_msg=msgspec.msgpack.encode(
                    {
                        "version": 2,
                        "tensor_id": remote.tensor_id,
                        "receipt": receipt,
                        "address": buffer.data_ptr(),
                        "size": size,
                        "memory": "cpu",
                        "metadata": self.agent_metadata,
                    }
                ),
            )
            status = await asyncio.wait_for(
                asyncio.shield(future), max(0, deadline - time.monotonic())
            )
            if status != "DONE":
                raise RuntimeError("NIXL WRITE failed at sender")
            return receipt, buffer[:size].view(
                tensor_dtype(request.embedding_dtype_str)
            ).view(request.embeddings_shape)
        except BaseException:
            item.abandoned = True
            # A completion may have raced with cancellation. Only the terminal
            # progress path can put the allocation into to_buffer_id.
            self.release_tensor(receipt)
            raise

    def release_tensor(self, tensor_id):
        buffer_id = self.to_buffer_id.pop(tensor_id, None)
        if buffer_id is not None:
            self.ring_buffer.release_buffer(buffer_id)

    async def close(self):
        if self.pending or self.to_buffer_id:
            raise RuntimeError("cannot close WRITE receiver with outstanding leases")
        self._closed = True
        await self._progress
        self.nixl_agent.deregister_memory(self.reg_descs)


class NixlReadEmbeddingSender(AbstractEmbeddingSender):
    """NIXL READ based embedding transfer sender.

    Uses nixl_connect.Connector which now natively provides a shared singleton
    Connection (NIXL agent) and reference-counted Remote agent lifecycle.
    """

    def __init__(self):
        self._nixl_connect = _load_nixl_connect()
        self.connector = self._nixl_connect.Connector()

    @_nvtx.annotate("mm:nixl:send_embeddings", color="magenta")
    async def send_embeddings(
        self, embeddings: torch.Tensor, stage_embeddings: bool = False
    ) -> tuple[TransferRequest, Awaitable[None]]:
        """
        Send precomputed embeddings.

        Args:
            embeddings: A torch.Tensor of the embeddings to send.
            stage_embeddings: A boolean indicating whether the embeddings should be staged for the transfer,
            if True, the embeddings may be used as transfer buffer and must not be released until the return future is completed.
            if False, the sender will copy the embeddings.
        Returns:
            A tuple containing the TransferRequest object and an awaitable that can be awaited to indicate the send is completed.
        """
        embeddings = transfer_tensor(embeddings)
        if stage_embeddings:
            transfer_buf = embeddings
        else:
            transfer_buf = embeddings.clone().detach()
        with _nvtx.annotate("mm:nixl:create_descriptor", color="pink"):
            descriptor = self._nixl_connect.Descriptor(transfer_buf)
        with _nvtx.annotate("mm:nixl:create_readable", color="pink"):
            try:
                readable_op = await self.connector.create_readable(descriptor)
            except Exception as exc:
                # If NIXL registration fails for a device tensor, fall back to CPU staging.
                if not transfer_buf.device.type == "cpu":
                    logger.warning(
                        "NIXL registration failed for %s tensor, falling back "
                        "to CPU staging: %s",
                        transfer_buf.device.type,
                        exc,
                    )
                    transfer_buf = transfer_buf.cpu()
                    descriptor = self._nixl_connect.Descriptor(transfer_buf)
                    readable_op = await self.connector.create_readable(descriptor)
                else:
                    raise
        request = TransferRequest(
            embeddings_shape=list(embeddings.shape),
            embedding_dtype_str=torch_dtype_to_string(embeddings.dtype),
            source_platform=selected_platform(),
            serialized_request=readable_op.metadata().model_dump(),
        )
        return request, readable_op.wait_for_completion()


class NixlReadEmbeddingReceiver(AbstractEmbeddingReceiver):
    """NIXL READ based embedding transfer receiver.

    Uses nixl_connect.Connector which now natively provides a shared singleton
    Connection (NIXL agent) and reference-counted Remote agent lifecycle.
    """

    def __init__(
        self,
        embedding_hidden_size: int = 8 * 1024,
        max_item_mm_token: int = 1024,
        max_items: int = 1024,
    ) -> None:
        super().__init__()
        self._nixl_connect = _load_nixl_connect()
        self.connector = self._nixl_connect.Connector()
        self.tensor_id_counter = 0
        self.aggregated_op_create_time = 0
        self.aggregated_op_wait_time = 0
        self.warmedup_descriptors: Queue[Any] = Queue()
        self.inuse_descriptors: dict[int, tuple[Any, bool]] = {}
        self._pending_reads: set[asyncio.Task] = set()
        connection = run_async(self.connector._create_connection)
        # Create descriptor for our allocated tensor
        for _ in range(max_items):
            encodings_tensor = torch.zeros(
                max_item_mm_token * embedding_hidden_size, dtype=torch.int8
            )
            descriptor = self._nixl_connect.Descriptor(encodings_tensor)
            descriptor.register_with_connector(connection)
            self.warmedup_descriptors.put(descriptor)

    @_nvtx.annotate("mm:nixl:receive_embeddings", color="magenta")
    async def receive_embeddings(
        self, request: TransferRequest
    ) -> tuple[int, torch.Tensor]:
        """
        Receive precomputed embeddings for a given request ID.

        Args:
            request: The TransferRequest object containing information to receive embeddings for.

        Returns:
            A tuple containing the tensor ID and the received embeddings as a torch.Tensor.
            Caller should invoke release_tensor(tensor_id) when the tensor is no longer needed to free up resources.
        """
        # Extract dynamic shape, metadata, and auxiliary data
        embeddings_shape = request.embeddings_shape
        embeddings_dtype = torch_dtype_from_string(request.embedding_dtype_str)
        readable_metadata = self._nixl_connect.RdmaMetadata.model_validate(
            request.serialized_request
        )

        size = tensor_nbytes(embeddings_shape, request.embedding_dtype_str)
        if (
            len(readable_metadata.descriptors) != 1
            or readable_metadata.descriptors[0].size != size
        ):
            raise ValueError("NIXL descriptor size does not match embedding metadata")
        remote = readable_metadata.descriptors[0]
        if str(remote.device) != "cpu" and (
            selected_platform() != "cuda"
            or request.source_platform not in (None, "cuda")
        ):
            raise ValueError("vendor embeddings must use host-staged NIXL descriptors")

        descriptor = None
        if not self.warmedup_descriptors.empty():
            candidate = self.warmedup_descriptors.get()
            if candidate._data_size >= size:
                descriptor = candidate
            else:
                self.warmedup_descriptors.put(candidate)
        dynamic_descriptor = descriptor is None
        original_descriptor_size = None
        if descriptor is None:
            encodings_tensor = torch.empty(embeddings_shape, dtype=embeddings_dtype)
            descriptor = self._nixl_connect.Descriptor(encodings_tensor)
        else:
            original_descriptor_size = descriptor._data_size
            descriptor._data_size = size
            encodings_tensor = (
                descriptor._data_ref[:size]
                .view(embeddings_dtype)
                .view(embeddings_shape)
            )

        async def read():
            try:
                operation = await self.connector.begin_read(
                    readable_metadata, descriptor
                )
                await operation.wait_for_completion()
            finally:
                if original_descriptor_size is not None:
                    descriptor._data_size = original_descriptor_size

        # A cancelled request must not recycle a destination while DMA is in flight.
        # Drain the owned operation, then return the buffer to its pool.
        task = asyncio.create_task(read())
        self._pending_reads.add(task)
        task.add_done_callback(self._pending_reads.discard)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:

            def recycle(completed):
                if not completed.cancelled() and completed.exception() is None:
                    if not dynamic_descriptor:
                        self.warmedup_descriptors.put(descriptor)

            task.add_done_callback(recycle)
            raise
        tensor_id = self.tensor_id_counter
        self.tensor_id_counter += 1
        self.inuse_descriptors[tensor_id] = (descriptor, dynamic_descriptor)
        return tensor_id, encodings_tensor

    def release_tensor(self, tensor_id: int) -> None:
        """
        Indicate that the tensor associated with the ID is no longer in use.

        Args:
            tensor_id: The ID of the tensor to release.
        """
        if tensor_id in self.inuse_descriptors:
            descriptor, dynamic_descriptor = self.inuse_descriptors[tensor_id]
            # Only put back to warmedup_descriptors if it's not dynamically created, as dynamic ones
            # may have varied shapes and putting them back may cause shape mismatch for future receive operations.
            if not dynamic_descriptor:
                self.warmedup_descriptors.put(descriptor)
            del self.inuse_descriptors[tensor_id]
