#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compare Go runtime thread counts with/without SPT_NOENV=1 at process start.

Run in the validated Ascend image with GOMAXPROCS=2. No NPU inference is run.
"""

import ctypes
import json
import os
import time

import psutil
import setproctitle


def main():
    setproctitle.setproctitle("m05-go-title-probe")
    import torch_npu  # noqa: F401

    ctypes.CDLL(
        "/usr/local/Ascend/cann-9.1.0/lib64/libllm_datadist.so",
        mode=ctypes.RTLD_GLOBAL,
    )
    from mooncake.engine import TransferEngine  # noqa: F401

    process = psutil.Process()
    for index in range(6):
        print(
            json.dumps(
                dict(
                    case=os.environ.get("SPT_NOENV"),
                    threads=process.num_threads(),
                    env_count=len(process.environ()),
                    rss=process.memory_info().rss,
                    t=index * 30,
                )
            ),
            flush=True,
        )
        time.sleep(30)


if __name__ == "__main__":
    main()
