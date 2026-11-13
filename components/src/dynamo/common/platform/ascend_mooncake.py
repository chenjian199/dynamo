# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve CANN symbols before loading the Ascend Mooncake extension.

In the tested CANN 9.1 / Mooncake 0.3.11.post1 image, importing Mooncake first
causes heap corruption during libascend_trace finalization. Loading CANN's
own dependency globally first also fixes a ctypes-only libtransfer_engine
reproducer. This is a library-load-order workaround, not allocator suppression.
"""

import ctypes
import os
from pathlib import Path

_cann_datadist = None


def prepare_mooncake_libraries():
    global _cann_datadist
    if _cann_datadist is None:
        # Resolve the active CANN environment, never a hard-coded old release.
        # Absolute candidates also avoid loading a same-named library from cwd.
        candidates = []
        for variable in ("ASCEND_HOME_PATH", "ASCEND_TOOLKIT_HOME"):
            if root := os.environ.get(variable):
                candidates.append(Path(root) / "lib64/libllm_datadist.so")
        candidates.extend(
            Path(directory) / "libllm_datadist.so"
            for directory in os.environ.get("LD_LIBRARY_PATH", "").split(":")
            if directory and Path(directory).is_absolute()
        )
        candidates.append(
            Path("/usr/local/Ascend/ascend-toolkit/latest/lib64/libllm_datadist.so")
        )
        library = next((path for path in candidates if path.is_file()), None)
        if library is None:
            raise RuntimeError(
                "Cannot find CANN libllm_datadist.so; initialize the active "
                "CANN environment before importing Mooncake"
            )
        _cann_datadist = ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
