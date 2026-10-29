#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-Pod device-memory byte parity; TCP carries metadata only, never tensors."""
import argparse
import base64
import json
import os
import socket
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--rank", type=int, choices=[0, 1], required=True)
p.add_argument("--host", required=True, help="This Pod IP")
p.add_argument("--peer", required=True, help="Rank 0 Pod IP")
p.add_argument("--port", type=int, default=29380)
a = p.parse_args()
if a.platform == "ascend":
    # The probe shares its allocated NPU with the idle vLLM worker. Give this
    # process a separate communicator port; do not change the serving process.
    os.environ["HCCL_NPU_SOCKET_PORT_RANGE"] = "auto"
import torch  # noqa: E402 - allow --help without a device runtime

if a.platform == "ascend":
    import torch_npu  # noqa: F401
    from dynamo.common.platform.ascend_mooncake import prepare_mooncake_libraries

    prepare_mooncake_libraries()
    from mooncake.engine import TransferEngine

    device = torch.npu
    kind = "npu"
    device.set_device(0)
    agent = TransferEngine()
    assert agent.initialize(a.host, "P2PHANDSHAKE", "ascend", "") == 0
else:
    from nixl._api import nixl_agent, nixl_agent_config

    device = torch.cuda
    kind = "cuda"
    device.set_device(0)
    agent = nixl_agent(
        "rdma-byte-probe-" + str(a.rank), nixl_agent_config(backends=["UCX"])
    )
if a.rank == 0:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((a.host, a.port))
    listener.listen(1)
    listener.settimeout(90)
    print("PROBE_LISTENING", flush=True)
    sock, _ = listener.accept()
    listener.close()
else:
    deadline = time.monotonic() + 90
    while True:
        try:
            sock = socket.create_connection((a.peer, a.port), timeout=5)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
sock.settimeout(90)
stream = sock.makefile("rwb")


def exchange(data):
    stream.write(json.dumps(data).encode() + b"\n")
    stream.flush()
    line = stream.readline(4 * 1024 * 1024)
    if not line or not line.endswith(b"\n"):
        raise RuntimeError("Peer closed or control record exceeded limit")
    return json.loads(line)


# Register one persistent buffer, matching the serving KV-cache lifetime.
# transfer-churn.py retains the separate allocator/registration stress probe.
capacity = 16777216
tensor = torch.zeros(capacity // 4, dtype=torch.int32, device=kind)
pointer = tensor.data_ptr()
device.synchronize()
if a.platform == "cuda":
    reg = agent.get_reg_descs([(pointer, capacity, 0, "")], "VRAM")
    agent.register_memory(reg)
    meta = base64.b64encode(agent.get_agent_metadata()).decode()
else:
    assert agent.register_memory(pointer, capacity) == 0
    meta = f"{a.host}:{agent.get_rpc_port()}"
remote = exchange({"pointer": pointer, "metadata": meta, "size": capacity})
assert remote["size"] == capacity
if a.platform == "cuda":
    peer_name = agent.add_remote_agent(base64.b64decode(remote["metadata"]))
cases = []
for iteration in range(2):
    for operation in ["READ", "WRITE"]:
        for size in [4, 4096, 524288, 4194304, 16777216]:
            source = 0 if operation == "READ" else 1
            expected = (
                torch.arange(size // 4, dtype=torch.int32, device=kind) + iteration + 41
            )
            view = tensor[: size // 4]
            view.copy_(expected) if a.rank == source else view.zero_()
            device.synchronize()
            assert exchange("ready") == "ready"
            if a.rank == 1:
                if a.platform == "cuda":
                    handle = agent.initialize_xfer(
                        operation,
                        agent.get_xfer_descs([(pointer, size, 0)], "VRAM"),
                        agent.get_xfer_descs([(remote["pointer"], size, 0)], "VRAM"),
                        peer_name,
                    )
                    agent.transfer(handle)
                    deadline = time.monotonic() + 45
                    while (state := agent.check_xfer_state(handle)) == "PROC":
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Native NIXL transfer stalled")
                        time.sleep(0.001)
                    assert state == "DONE", state
                    agent.release_xfer_handle(handle)
                else:
                    transfer = getattr(
                        agent, "batch_transfer_sync_" + operation.lower()
                    )
                    assert (
                        transfer(
                            remote["metadata"], [pointer], [remote["pointer"]], [size]
                        )
                        == 0
                    )
            assert exchange("transferred") == "transferred"
            device.synchronize()
            assert torch.equal(view, expected), (operation, size, iteration, a.rank)
            assert exchange("verified") == "verified"
            del view, expected
            cases.append(
                {"operation": operation, "bytes": size, "iteration": iteration}
            )
            print("CASE", cases[-1], flush=True)
if a.platform == "cuda":
    agent.remove_remote_agent(peer_name)
    agent.deregister_memory(reg)
else:
    assert agent.unregister_memory(pointer) == 0
# Release native channels before device storage is returned to the allocator.
del agent
del tensor
device.synchronize()
assert device.memory_allocated() == 0
print(
    "RDMA_PROBE_RESULT="
    + json.dumps(
        {
            "passed": True,
            "platform": a.platform,
            "rank": a.rank,
            "host": a.host,
            "peer": a.peer,
            "cases": cases,
            "device_allocated_after": device.memory_allocated(),
            "environment": {
                k: os.environ.get(k)
                for k in ["UCX_TLS", "UCX_NET_DEVICES", "UCX_IB_GPU_DIRECT_RDMA"]
            },
        }
    ),
    flush=True,
)
stream.close()
sock.close()
