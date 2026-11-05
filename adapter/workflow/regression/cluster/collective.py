#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Two-node device all-reduce/broadcast correctness, using one allocated card each."""

import datetime
import json
import os
import resource
import time

import torch
import torch.distributed as dist

platform = os.environ["DYN_PLATFORM"]
rank = int(os.environ["RANK"])
if platform == "ascend":
    import torch_npu  # noqa: F401

    api, kind, backend = torch.npu, "npu", "hccl"
else:
    api, kind, backend = torch.cuda, "cuda", "nccl"
assert api.device_count() == 1
api.set_device(0)
print(
    json.dumps(
        {
            "rank": rank,
            "backend": backend,
            "master": os.environ["MASTER_ADDR"],
            "memlock": resource.getrlimit(resource.RLIMIT_MEMLOCK),
        }
    ),
    flush=True,
)
dist.init_process_group(backend, timeout=datetime.timedelta(seconds=180))
results = []
try:
    for count in (1024, 1048576, 16777216):
        start = time.monotonic()
        for _ in range(3):
            tensor = torch.full(
                (count,), rank + 1, dtype=torch.float32, device=f"{kind}:0"
            )
            dist.all_reduce(tensor)
            assert bool(torch.all(tensor == 3).item())
            tensor.fill_(rank + 7)
            dist.broadcast(tensor, src=0)
            assert bool(torch.all(tensor == 7).item())
        api.synchronize()
        results.append(
            {
                "bytes": count * 4,
                "iterations": 3,
                "elapsed_seconds": time.monotonic() - start,
            }
        )
    dist.barrier()
    print(
        "REGRESSION_RESULT="
        + json.dumps(
            {"passed": True, "rank": rank, "backend": backend, "cases": results}
        ),
        flush=True,
    )
finally:
    dist.destroy_process_group()
