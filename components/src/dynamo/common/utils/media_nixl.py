# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Derived from NVIDIA Dynamo. Modified by HEYI.

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, Literal, Tuple, overload

import numpy as np
import torch

from dynamo.common.multimodal.tensor_contract import (
    selected_platform,
    tensor_dtype,
    tensor_nbytes,
)

if TYPE_CHECKING:
    from dynamo import nixl_connect

logger = logging.getLogger(__name__)


def validate_media_descriptor(decoded_meta: Dict[str, Any]) -> tuple[str, torch.dtype]:
    """Validate wire layout before allocating or constructing an RDMA operation."""
    if decoded_meta.get("schema_version", 1) != 1:
        raise ValueError("unsupported media descriptor schema")
    descriptor = decoded_meta["nixl_descriptor"]
    shape = decoded_meta["shape"]
    dtype = decoded_meta.get("dtype", "uint8")
    size = tensor_nbytes(shape, dtype)
    if type(descriptor["size"]) is not int or descriptor["size"] != size:
        raise ValueError("media descriptor byte size does not match shape/dtype")
    if type(descriptor["addr"]) is not int or descriptor["addr"] <= 0:
        raise ValueError("media descriptor requires a positive address")
    memory = descriptor.get("mem_type", "dram").lower()
    if memory == "dram":
        return "cpu", tensor_dtype(dtype)
    platform = decoded_meta.get("source_platform", "cuda")
    if (
        memory not in ("vram", "cuda")
        or platform != "cuda"
        or selected_platform() != "cuda"
    ):
        raise ValueError(
            "device media requires CUDA descriptors; stage vendor media on CPU"
        )
    ordinal = descriptor.get("device_id", 0)
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError("invalid remote CUDA device ordinal")
    return f"cuda:{ordinal}", tensor_dtype(dtype)


@overload
async def read_decoded_media_via_nixl(
    connector: nixl_connect.Connector,
    decoded_meta: Dict[str, Any],
    return_metadata: Literal[False] = False,
) -> np.ndarray:
    ...


@overload
async def read_decoded_media_via_nixl(
    connector: nixl_connect.Connector,
    decoded_meta: Dict[str, Any],
    return_metadata: Literal[True],
) -> Tuple[np.ndarray, Dict[str, Any] | None]:
    ...


async def read_decoded_media_via_nixl(
    connector: nixl_connect.Connector,
    decoded_meta: Dict[str, Any],
    return_metadata: bool = False,
) -> np.ndarray | Tuple[np.ndarray, Dict[str, Any] | None]:
    """
    Read pre-decoded media data via NIXL RDMA transfer, into a CPU numpy array.

    Args:
        connector: Initialized NIXL connector for RDMA operations.
        decoded_meta: Metadata dict from the frontent, containing nixl_metadata, shape, dtype, nixl_descriptor, and metadata.

    Returns:
        np.ndarray containing the transferred media data.
        Dict[str, Any] containing the media metadata.
    """
    remote_device, dtype = validate_media_descriptor(decoded_meta)
    try:
        from dynamo import nixl_connect
        from dynamo.nixl_connect import (
            OperationKind,
            RdmaMetadata,
            SerializedDescriptor,
        )
    except ImportError as exc:
        raise RuntimeError(
            "NIXL is required to read decoded media via frontend decoding; "
            "install dynamo.nixl_connect to enable RDMA media transfers."
        ) from exc

    rdma_metadata = decoded_meta["nixl_metadata"]
    descriptor = decoded_meta["nixl_descriptor"]
    rdma_metadata = RdmaMetadata(
        descriptors=[
            SerializedDescriptor(
                device=remote_device,
                ptr=descriptor["addr"],
                size=descriptor["size"],
            )
        ],
        nixl_metadata=rdma_metadata,
        notification_key=str(uuid.uuid4()),
        operation_kind=int(OperationKind.READ),
    )

    # Create empty tensor to receive RDMA data
    shape = decoded_meta["shape"]
    alloc_start = time.perf_counter()
    tensor = torch.empty(shape, dtype=dtype)
    alloc_end = time.perf_counter()
    local_descriptor = nixl_connect.Descriptor(tensor)

    read_start = time.perf_counter()
    read_op = await connector.begin_read(rdma_metadata, local_descriptor)
    await read_op.wait_for_completion()
    read_end = time.perf_counter()

    logger.debug(
        f"Loaded media via NIXL RDMA: shape={shape}, "
        f"read_time={read_end - read_start:.4f}s, "
        f"alloc_time={alloc_end - alloc_start:.6f}s"
    )

    array = tensor.numpy()  # zero-copy
    if array.dtype == np.uint8 and array.ndim in (3, 4) and array.shape[-1] == 4:
        array = array[..., :3]  # RGB(A) image/video only; preserve audio samples.
    if return_metadata:
        return array, decoded_meta.get("metadata")
    else:
        return array
