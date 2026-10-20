# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# Derived from NVIDIA Dynamo. Modified by HEYI.
# SPDX-License-Identifier: Apache-2.0

# flake8: noqa
import logging
import os

logger = logging.getLogger(__name__)

# nixl needs to be loaded before any other imports to ensure that the nixl shared object is available for the KVBM core.
if (
    os.environ.get("DYN_PLATFORM") not in ("metax", "ascend")
    and os.environ.get("DYN_KVBM_TRANSFER_BACKEND") != "platform"
):
    import nixl

    logger.debug("Loaded nixl API module: %s", nixl._api)

from kvbm._core import BlockManager as BlockManager
from kvbm._core import KvbmLeader as KvbmLeader
from kvbm._core import KvbmWorker as KvbmWorker
