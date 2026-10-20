# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# Derived from NVIDIA Dynamo. Modified by HEYI.
# SPDX-License-Identifier: Apache-2.0

"""
Implementation of vLLM KV cache manager protocol.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import torch
from kvbm.platform_events import PlatformEvent, block_axis, tensor_platform
from kvbm.utils import is_dyn_runtime_enabled
from vllm.distributed.kv_transfer.kv_connector.v1.base import KVConnectorMetadata
from vllm.model_executor.models.utils import extract_layer_index
from vllm.utils.torch_utils import STR_DTYPE_TO_TORCH_DTYPE

if TYPE_CHECKING:
    from vllm.attention.backends.abstract import AttentionMetadata
    from vllm.config import VllmConfig
    from vllm.forward_context import ForwardContext


# from kvbm.vllm_integration.kv_cache_utils import KvbmCacheBlocks
# from kvbm.vllm_integration.rust import BlockManager
# from kvbm.vllm_integration.rust import (
#     KvConnectorMetadata as RustKvConnectorMetadata,
#     KvConnectorWorker as RustKvConnectorWorker,
# )

from kvbm.vllm_integration.rust import KvConnectorWorker as RustKvConnectorWorker


@dataclass
class KvTensorLayout:
    """Semantic description of the KV cache tensor layout.

    Python derives this from VllmConfig (which has the model architecture)
    so that Rust does not need to guess from raw tensor shapes.

    For MLA models, outer_dim and inner_dim are set explicitly because Rust's
    shape-based inference cannot distinguish the fused KV latent axis from the
    block/page axis.  For standard attention the fields are None, which tells
    Rust to fall back to its own contiguity-based inference (already correct).
    """

    outer_dim: Optional[int]  # None = let Rust detect; 1 for MLA
    inner_dim: Optional[int]  # None = let Rust detect; head_size for MLA

    @classmethod
    def from_vllm_config(
        cls, vllm_config: "VllmConfig", shape: "torch.Size", use_mla: bool = False
    ) -> "KvTensorLayout":
        if use_mla:
            # MLA tensors are 3D: [num_blocks, page_size, head_size]
            # No outer_dim axis — K and V are fused into a single latent.
            return cls(outer_dim=1, inner_dim=shape[-1])
        else:
            # Standard attention: Rust already infers outer_dim and inner_dim
            # correctly from tensor strides/contiguity.  Don't guess here —
            # the block dimension can be at position 0 or 1 depending on the
            # attention backend, so shape[1] is not reliably outer_dim.
            return cls(outer_dim=None, inner_dim=None)


DistributedRuntime = None
if is_dyn_runtime_enabled():
    from dynamo.runtime import DistributedRuntime


class DynamoConnectorMetadata(KVConnectorMetadata):
    def __init__(self, metadata: bytes):
        assert isinstance(metadata, bytes)
        self.metadata = metadata


class KvConnectorWorker:
    def __init__(self, vllm_config: "VllmConfig", engine_id: str, **kwargs):
        drt: Optional[object] = kwargs.get("drt")

        if drt is None and is_dyn_runtime_enabled():
            drt = DistributedRuntime.detached()
        else:
            drt = None

        self.drt = drt

        self.vllm_config = vllm_config
        self._connector = RustKvConnectorWorker(self.drt, engine_id)

    # Worker

    def register_kv_caches(self, kv_caches: dict[str, torch.Tensor]):
        """
        Initialize with the KV caches. Useful for pre-registering the
        KV Caches in the KVConnector (e.g. for NIXL).

        Args: kv_caches:
            dictionary of layer names, kv cache
        """

        cache_config = self.vllm_config.cache_config

        # Create ordered list of (layer_name, tensor) tuples sorted by layer index
        ordered_kv_caches = [
            (layer_name, tensor)
            for layer_name, tensor in sorted(
                kv_caches.items(), key=lambda item: extract_layer_index(item[0])
            )
        ]

        if not ordered_kv_caches:
            raise ValueError("KVBM requires at least one KV tensor")
        # Ascend exposes a (K, V) tensor pair per layer. Register each tensor
        # separately, but record one producing event after the full layer.
        self.layer_parts = {}
        self.events = {}
        flattened = []
        events = []
        for layer, cache in ordered_kv_caches:
            parts = list(cache) if isinstance(cache, (tuple, list)) else [cache]
            if not parts or any(not isinstance(t, torch.Tensor) for t in parts):
                raise ValueError("KVBM requires dense KV tensors or a K/V tensor pair")
            if len(parts) > 1 and (
                len(parts) != 2 or tensor_platform(parts[0]) != "ascend"
            ):
                raise ValueError(
                    "Separate K/V registration is only supported for Ascend"
                )
            names = [layer] if len(parts) == 1 else [f"{layer}.key", f"{layer}.value"]
            event = PlatformEvent(parts[0])
            self.events[layer] = event
            self.layer_parts[layer] = names
            flattened.extend(zip(names, parts))
            events.extend([event] * len(parts))
        ordered_kv_caches = flattened

        # Get first tensor to extract common properties
        first_tensor = ordered_kv_caches[0][1]
        shape = first_tensor.shape

        # Validate all tensors have same shape
        if not all(t.shape == shape for _, t in ordered_kv_caches):
            raise NotImplementedError(
                "Hybrid models with different KV cache shapes are not supported yet."
            )

        page_size = cache_config.block_size
        use_mla = getattr(self.vllm_config.model_config, "use_mla", False)

        # Use the engine's actual block count; do not infer it from axis sizes.
        num_device_blocks = cache_config.num_gpu_blocks
        # vLLM-Ascend 0.23.0 multiprocess workers do not receive the scheduler's
        # computed num_gpu_blocks. Its dense K/V tensors have the documented
        # [blocks, block_size, kv_heads, head_size] layout. Use that named axis,
        # not a max(shape) heuristic. Remove when the worker config carries it.
        if num_device_blocks is None and tensor_platform(first_tensor) == "ascend":
            if len(shape) != 4 or shape[1] != page_size:
                raise ValueError(
                    "Ascend KVBM needs explicit block count for this layout"
                )
            num_device_blocks = shape[0]
        if not isinstance(num_device_blocks, int) or num_device_blocks <= 0:
            raise ValueError("Engine must provide its actual KV block count")
        device_id = first_tensor.device.index

        layout = KvTensorLayout.from_vllm_config(self.vllm_config, shape, use_mla)

        # Determine cache dtype
        if cache_config.cache_dtype == "auto":
            kv_cache_dtype = self.vllm_config.model_config.dtype
        else:
            kv_cache_dtype = STR_DTYPE_TO_TORCH_DTYPE[cache_config.cache_dtype]

        platform = tensor_platform(first_tensor)
        backend = os.environ.get("DYN_KVBM_TRANSFER_BACKEND")
        native_platform = platform != "cuda" or backend == "platform"
        expected_device_dtype = (first_tensor.device, first_tensor.dtype)
        expected_provider_width = (platform, kv_cache_dtype.itemsize)
        axes = []
        for _, tensor in ordered_kv_caches:
            if (tensor.device, tensor.dtype) != expected_device_dtype:
                raise ValueError("KV layers must share device and dtype")
            descriptor = (tensor_platform(tensor), tensor.element_size())
            if descriptor != expected_provider_width:
                raise ValueError(
                    "KV tensor provider/dtype differs from engine configuration"
                )
            axes.append(block_axis(tensor, num_device_blocks))
        kwargs = {"events": events}
        if native_platform:
            kwargs.update(platform=platform, block_axes=axes)

        # Register with connector using ordered data
        self._connector.register_kv_caches(
            num_device_blocks,
            page_size,
            device_id,
            kv_cache_dtype.itemsize,
            ordered_kv_caches,
            [],
            outer_dim=layout.outer_dim,
            inner_dim=layout.inner_dim,
            **kwargs,
        )

    def bind_connector_metadata(self, data: bytes) -> None:
        """Set the connector metadata from the scheduler.

        This function should be called by the model runner every time
        before the model execution. The metadata will be used for runtime
        KV cache loading and saving.

        Args:
            connector_metadata (dict): the connector metadata.
        """
        self._connector.bind_connector_metadata(data)

    def clear_connector_metadata(self) -> None:
        """Clear the connector metadata.

        This function should be called by the model runner every time
        after the model execution.
        """
        self._connector.clear_connector_metadata()

    def start_load_kv(self, forward_context: "ForwardContext", **kwargs) -> None:
        """
        Start loading the KV cache from the connector to vLLM's paged
        KV buffer. This is called from the forward context before the
        forward pass to enable async loading during model execution.

        Args:
            forward_context (ForwardContext): the forward context.
            **kwargs: additional arguments for the load operation

        Note:
            The number of elements in kv_caches and layer_names should be
            the same.

        """
        pass

    def save_kv_layer(
        self,
        layer_name: str,
        kv_layer: torch.Tensor,
        attn_metadata: "AttentionMetadata",
        **kwargs,
    ) -> None:
        """
        Start saving a layer of KV cache from vLLM's paged buffer
        to the connector. This is called from within attention layer to
        enable async copying during execution.

        Args:
            layer_name (str): the name of the layer.
            kv_layer (torch.Tensor): the paged KV buffer of the current
                layer in vLLM.
            attn_metadata (AttentionMetadata): the attention metadata.
            **kwargs: additional arguments for the save operation.
        """
        self.events[layer_name].record()
        for part in self.layer_parts[layer_name]:
            self._connector.save_kv_layer(part, kv_layer)

    def get_finished(
        self, finished_req_ids: set[str]
    ) -> tuple[Optional[set[str]], Optional[set[str]]]:
        """
        Notifies worker-side connector ids of requests that have
        finished generating tokens on the worker.
        The scheduler process (via the MultiprocExecutor) will use this output
        to track which workers are done.

        Returns:
            ids of requests that have finished asynchronous transfer
            (requests that previously returned True from request_finished()),
            tuple of (sending/saving ids, recving/loading ids).
            The finished saves/sends req ids must belong to a set provided in a
            call to this method (this call or a prior one).
        """
        # finished_ids = [id for id in finished_req_ids]
        # return set(sending_ids), set(receiving_ids)
        return self._connector.get_finished(finished_req_ids)
