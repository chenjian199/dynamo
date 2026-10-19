#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real Mooncake device registration/copy cycles and optional pending-peer fault."""

import argparse
import json
import multiprocessing as mp
import os
import signal
import socket
import time
from pathlib import Path


def recv(pipe, timeout=90):
    if not pipe.poll(timeout):
        raise TimeoutError("Mooncake probe peer deadline")
    return pipe.recv()


def worker(rank, pipe, args):
    os.environ[
        "ASCEND_RT_VISIBLE_DEVICES"
        if args.platform == "ascend"
        else "CUDA_VISIBLE_DEVICES"
    ] = args.devices.split(",")[rank]
    import psutil
    import torch

    if args.platform == "ascend":
        import torch_npu  # noqa: F401
    if args.platform == "ascend":
        from dynamo.common.platform.ascend_mooncake import prepare_mooncake_libraries

        prepare_mooncake_libraries()
    from mooncake.engine import TransferEngine

    device = torch.npu if args.platform == "ascend" else torch.cuda
    device.set_device(0)
    engine = TransferEngine()
    hostname = socket.gethostbyname(socket.gethostname())
    protocol = "ascend" if args.platform == "ascend" else "maca"
    assert engine.initialize(hostname, "P2PHANDSHAKE", protocol, "") == 0
    result = dict(status="failed", rank=rank, cycles=[])
    process = psutil.Process()
    for iteration in range(args.iterations if not args.fault else 1):
        size = 64 * 1024**2 if args.fault else 4 * 1024**2
        tensor = torch.full(
            (size // 4,),
            42 + iteration if rank == 0 else 0,
            dtype=torch.int32,
            device="npu" if args.platform == "ascend" else "cuda",
        )
        device.synchronize()
        pointer = tensor.data_ptr()
        assert engine.register_memory(pointer, size) == 0
        pipe.send(dict(session=f"{hostname}:{engine.get_rpc_port()}", pointer=pointer))
        remote = recv(pipe)
        if rank == 1:
            assert (
                engine.batch_transfer_sync_read(
                    remote["session"],
                    [pointer],
                    [remote["pointer"]],
                    [4 if args.fault else size],
                )
                == 0
            )
            device.synchronize()
            assert tensor[0].item() == 42 + iteration
            if args.fault:
                pipe.send("warmup-done")
                assert recv(pipe) == "peer-stopped"
                batch = engine.batch_transfer_async_read(
                    remote["session"], [pointer], [remote["pointer"]], [size]
                )
                if batch == 0:
                    raise RuntimeError("Native submit failed before injection")
                state = engine.transfer_check_status(batch)
                if state != 0:
                    raise RuntimeError(f"Injection missed pending state: {state}")
                pipe.send(dict(event="pending", batch=batch, state=state))
                assert recv(pipe) == "peer-killed"
                start = time.monotonic()
                while (state := engine.transfer_check_status(batch)) == 0:
                    if time.monotonic() - start > 45:
                        raise TimeoutError("Native operation did not terminate")
                    time.sleep(0.01)
                result["fault"] = dict(
                    pending_state=0,
                    terminal_state=state,
                    seconds=time.monotonic() - start,
                )
                if state == 1:
                    device.synchronize()
                    if not bool(torch.all(tensor == 42 + iteration)):
                        raise RuntimeError(
                            "Completed transfer returned corrupt device data"
                        )
                    result["fault"][
                        "outcome"
                    ] = "completed_with_byte_parity_after_peer_exit"
                elif state == -1:
                    result["fault"]["outcome"] = "terminal_native_error"
                else:
                    # A timeout is not a guarantee of device quiescence. Leave
                    # registrations intact; parent tears down the owned process.
                    raise RuntimeError(f"No confirmed terminal failure: {state}")
            else:
                assert bool(torch.all(tensor == 42 + iteration))
            pipe.send("verified")
        else:
            assert recv(pipe) == "verified"
        device.synchronize()
        assert engine.unregister_memory(pointer) == 0
        del tensor
        device.synchronize()
        result["cycles"].append(
            dict(
                iteration=iteration,
                rss=process.memory_info().rss,
                fds=process.num_fds(),
                allocated=device.memory_allocated(),
            )
        )
        pipe.send("released")
        assert recv(pipe) == "released"
    samples = result["cycles"]
    if any(sample["allocated"] for sample in samples):
        raise RuntimeError("Device allocation retained after deregistration")
    if len(samples) >= 4 and (
        samples[-1]["fds"] > samples[2]["fds"] + 4
        or samples[-1]["rss"] > samples[2]["rss"] + 64 * 1024**2
    ):
        raise RuntimeError("Native resources grew beyond post-warmup limits")
    result["status"] = "passed"
    (args.output / f"rank-{rank}.json").write_text(json.dumps(result, indent=2) + "\n")
    del engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["metax", "ascend"], required=True)
    parser.add_argument("--devices", default="2,3")
    parser.add_argument("--iterations", type=int, default=64)
    parser.add_argument("--fault", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    context = mp.get_context("spawn")
    pipes = [context.Pipe() for _ in range(2)]
    children = [
        context.Process(target=worker, args=(i, pipes[i][1], args)) for i in range(2)
    ]
    result = dict(
        status="failed",
        platform=args.platform,
        fault=args.fault,
        iterations=args.iterations,
        events=[],
    )
    try:
        for child in children:
            child.start()
        for _ in range(args.iterations if not args.fault else 1):
            data = [recv(pair[0]) for pair in pipes]
            for i in range(2):
                pipes[i][0].send(data[1 - i])
            if args.fault:
                assert recv(pipes[1][0]) == "warmup-done"
                os.kill(children[0].pid, signal.SIGSTOP)
                pipes[1][0].send("peer-stopped")
                event = recv(pipes[1][0])
                assert event["state"] == 0
                result["events"].append(event)
                children[0].kill()
                children[0].join(10)
                pipes[1][0].send("peer-killed")
                assert recv(pipes[1][0]) == "verified"
                assert recv(pipes[1][0]) == "released"
                pipes[1][0].send("released")
            else:
                assert recv(pipes[1][0]) == "verified"
                pipes[0][0].send("verified")
                for pair in pipes:
                    assert recv(pair[0]) == "released"
                for pair in pipes:
                    pair[0].send("released")
        for child in children:
            child.join(30)
        expected = [-signal.SIGKILL, 0] if args.fault else [0, 0]
        if [child.exitcode for child in children] != expected:
            raise RuntimeError("Native child did not exit cleanly")
        result["status"] = "passed"
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
            child.join()
        result["exit_codes"] = [child.exitcode for child in children]
        (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
