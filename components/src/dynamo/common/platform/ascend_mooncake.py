# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve CANN symbols before loading the Ascend Mooncake extension.

In the tested CANN 9.1 / Mooncake 0.3.11.post1 image, importing Mooncake first
causes heap corruption during libascend_trace finalization. Loading CANN's
own dependency globally first also fixes a ctypes-only libtransfer_engine
reproducer. This is a library-load-order workaround, not allocator suppression.
"""

import ctypes

_cann_datadist = None


def prepare_mooncake_libraries():
    global _cann_datadist
    if _cann_datadist is None:
        _cann_datadist = ctypes.CDLL(
            "/usr/local/Ascend/cann-9.1.0/lib64/libllm_datadist.so",
            mode=ctypes.RTLD_GLOBAL,
        )
