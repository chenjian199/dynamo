#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded two-process NIXL VRAM READ/WRITE parity probe, independent of vLLM."""

import argparse
import json
import multiprocessing as mp
import os
import time
from pathlib import Path


def receive(pipe):
    if not pipe.poll(60):
        raise TimeoutError("probe peer did not reply")
    return pipe.recv()


def worker(rank, pipe, args):
    os.environ["CUDA_VISIBLE_DEVICES"] = args.devices.split(",")[rank]
    # Spawned processes must select their device before importing vendor runtimes.
    import torch
    from nixl._api import nixl_agent, nixl_agent_config

    agent = nixl_agent(f"probe-{rank}", nixl_agent_config(num_threads=0, backends=[]))
    agent.create_backend("UCX", {"ucx_error_handling_mode": "peer"})
    results = []
    try:
        for iteration in range(args.iterations):
            for operation in ("READ", "WRITE"):
                for size in (4, 4096, 524288, 4194304):
                    source = 0 if operation == "READ" else 1
                    expected = 42 + iteration
                    tensor = torch.full(
                        (size // 4,),
                        expected if rank == source else 0,
                        dtype=torch.int32,
                        device="cuda",
                    )
                    torch.cuda.synchronize()
                    desc = [(tensor.data_ptr(), size, 0, "")]
                    registration = agent.get_reg_descs(desc, "VRAM")
                    agent.register_memory(registration)
                    pipe.send((agent.get_agent_metadata(), desc))
                    metadata, remote = receive(pipe)
                    name = agent.add_remote_agent(metadata)
                    if rank == 1:
                        handle = agent.initialize_xfer(
                            operation,
                            agent.get_xfer_descs([d[:3] for d in desc], "VRAM"),
                            agent.get_xfer_descs([d[:3] for d in remote], "VRAM"),
                            name,
                        )
                        agent.transfer(handle)
                        deadline = time.monotonic() + 30
                        while (state := agent.check_xfer_state(handle)) == "PROC":
                            if time.monotonic() > deadline:
                                raise TimeoutError("NIXL transfer stalled")
                            time.sleep(0.001)
                        if state != "DONE":
                            raise RuntimeError(f"Unexpected transfer state {state}")
                        agent.release_xfer_handle(handle)
                        pipe.send("done")
                    else:
                        assert receive(pipe) == "done"
                    torch.cuda.synchronize()
                    if not bool(torch.all(tensor == expected)):
                        raise RuntimeError(
                            f"Data mismatch: {operation}, {size}, rank {rank}"
                        )
                    pipe.send("verified")
                    assert receive(pipe) == "verified"
                    agent.remove_remote_agent(name)
                    agent.deregister_memory(registration)
                    results.append(
                        {"iteration": iteration, "operation": operation, "bytes": size}
                    )
        (args.output / f"rank-{rank}.maps.txt").write_text(
            Path("/proc/self/maps").read_text()
        )
        (args.output / f"rank-{rank}.json").write_text(
            json.dumps({"status": "passed", "cases": results}, indent=2) + "\n"
        )
    finally:
        # NIXL must release registrations and worker threads before torch teardown.
        del agent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", default="0,1")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.devices.split(",")) != 2 or args.iterations < 1:
        parser.error("provide two device ordinals and positive iterations")
    args.output.mkdir(parents=True, exist_ok=False)
    context = mp.get_context("spawn")
    left, right = context.Pipe()
    children = [
        context.Process(target=worker, args=(rank, pipe, args))
        for rank, pipe in enumerate((left, right))
    ]
    try:
        for child in children:
            child.start()
        deadline = time.monotonic() + 180
        for child in children:
            child.join(max(0, deadline - time.monotonic()))
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
            child.join()
    codes = [child.exitcode for child in children]
    report = {
        "status": "passed" if codes == [0, 0] else "failed",
        "exit_codes": codes,
        "environment": {
            name: os.environ.get(name)
            for name in ("UCX_PROTO_ENABLE", "UCX_TLS", "UCX_MEMTYPE_CACHE")
        },
        "devices": args.devices,
        "iterations": args.iterations,
    }
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    if report["status"] != "passed":
        raise RuntimeError(f"Probe failed: {report}")


if __name__ == "__main__":
    main()
