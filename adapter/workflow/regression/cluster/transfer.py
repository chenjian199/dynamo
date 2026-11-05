#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-node device-buffer parity; JSON metadata over TCP, data via vendor transport."""

import base64
import json
import os
import socket
import time

import torch

platform = os.environ["DYN_PLATFORM"]
rank = int(os.environ["RANK"])
if platform == "ascend":
    import torch_npu  # noqa: F401

    from dynamo.common.platform.ascend_mooncake import prepare_mooncake_libraries

    prepare_mooncake_libraries()
    from mooncake.engine import TransferEngine

    api, kind = torch.npu, "npu"
else:
    from nixl._api import nixl_agent, nixl_agent_config

    api, kind = torch.cuda, "cuda"
assert api.device_count() == 1
api.set_device(0)
address = (os.environ["MASTER_ADDR"], int(os.environ["MASTER_PORT"]))
if rank == 0:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.settimeout(180)
    listener.bind(address)
    listener.listen(1)
    sock, _ = listener.accept()
    listener.close()
else:
    deadline = time.monotonic() + 180
    while True:
        try:
            sock = socket.create_connection(address, timeout=5)
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
sock.settimeout(120)
wire = sock.makefile("rwb")


def exchange(value):
    # Small metadata only; never serialize or copy tensor payloads here.
    wire.write(json.dumps(value).encode() + b"\n")
    wire.flush()
    data = wire.readline(4 * 1024 * 1024)
    if not data.endswith(b"\n"):
        raise RuntimeError("Missing peer metadata or oversized frame")
    return json.loads(data)


if platform == "ascend":
    engine = TransferEngine()
    assert engine.initialize(os.environ["NODE_IP"], "P2PHANDSHAKE", "ascend", "") == 0
else:
    engine = nixl_agent(f"regression-{rank}", nixl_agent_config(backends=[]))
    engine.create_backend("UCX", {"ucx_error_handling_mode": "peer"})
results = []
try:
    for size in (4096, 4 * 1024**2, 64 * 1024**2):
        for operation in ["READ"] if platform == "ascend" else ["READ", "WRITE"]:
            source = 0 if operation == "READ" else 1
            tensor = torch.full(
                (size // 4,),
                42 if rank == source else 0,
                dtype=torch.int32,
                device=f"{kind}:0",
            )
            api.synchronize()
            pointer = tensor.data_ptr()
            if platform == "ascend":
                assert engine.register_memory(pointer, size) == 0
                local = {
                    "pointer": pointer,
                    "session": f"{os.environ['NODE_IP']}:{engine.get_rpc_port()}",
                }
            else:
                registration = engine.get_reg_descs([(pointer, size, 0, "")], "VRAM")
                engine.register_memory(registration)
                local = {
                    "pointer": pointer,
                    "metadata": base64.b64encode(engine.get_agent_metadata()).decode(),
                }
            remote = exchange(local)
            if platform == "cuda":
                peer = engine.add_remote_agent(base64.b64decode(remote["metadata"]))
            start = time.monotonic()
            if rank == 1:
                if platform == "ascend":
                    assert (
                        engine.batch_transfer_sync_read(
                            remote["session"], [pointer], [remote["pointer"]], [size]
                        )
                        == 0
                    )
                else:
                    handle = engine.initialize_xfer(
                        operation,
                        engine.get_xfer_descs([(pointer, size, 0)], "VRAM"),
                        engine.get_xfer_descs([(remote["pointer"], size, 0)], "VRAM"),
                        peer,
                    )
                    engine.transfer(handle)
                    deadline = time.monotonic() + 60
                    while (state := engine.check_xfer_state(handle)) == "PROC":
                        if time.monotonic() > deadline:
                            raise TimeoutError("NIXL transfer deadline")
                        time.sleep(0.001)
                    assert state == "DONE", state
                    engine.release_xfer_handle(handle)
            assert exchange("done") == "done"
            api.synchronize()
            assert bool(torch.all(tensor == 42).item()), "device payload mismatch"
            assert exchange("verified") == "verified"
            results.append(
                {
                    "operation": operation,
                    "bytes": size,
                    "elapsed_seconds": time.monotonic() - start,
                }
            )
            if platform == "ascend":
                assert engine.unregister_memory(pointer) == 0
            else:
                engine.remove_remote_agent(peer)
                engine.deregister_memory(registration)
            del tensor
            api.synchronize()
    print(
        "REGRESSION_RESULT="
        + json.dumps(
            {"passed": True, "platform": platform, "rank": rank, "cases": results}
        ),
        flush=True,
    )
finally:
    del engine
    wire.close()
    sock.close()
