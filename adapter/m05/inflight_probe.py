#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""NIXL fault after a real GPU transfer reports PROC; kill only the owned peer.

Uses TCP deliberately so the paused peer cannot finish the READ through GPU IPC.
A process timeout is a failure, never evidence that buffers were drained.
"""

import argparse
import json
import multiprocessing as mp
import os
import signal
import time
from pathlib import Path


def recv(pipe, timeout=90):
    if not pipe.poll(timeout):
        raise TimeoutError("Probe control deadline exceeded")
    return pipe.recv()


def worker(rank, pipe, args):
    os.environ["CUDA_VISIBLE_DEVICES"] = args.devices.split(",")[rank]
    import torch
    from nixl._api import nixl_agent, nixl_agent_config

    tensor = torch.full(
        (64 * 1024**2 // 4,), 42 if rank == 0 else 0, dtype=torch.int32, device="cuda"
    )
    torch.cuda.synchronize()
    agent = nixl_agent(
        f"inflight-{rank}-{os.getpid()}", nixl_agent_config(num_threads=1, backends=[])
    )
    backend = {"ucx_error_handling_mode": "peer"}
    if args.force_close:
        backend["ucx_ep_close_force"] = "yes"
    agent.create_backend("UCX", backend)
    desc = [(tensor.data_ptr(), tensor.numel() * 4, 0, "")]
    registered = agent.get_reg_descs(desc, "VRAM")
    agent.register_memory(registered)
    pipe.send(dict(metadata=agent.get_agent_metadata(), desc=desc))
    if rank == 0:
        recv(pipe, 180)  # Parent owns the stop/kill. Keep buffer and agent alive.
        raise RuntimeError("Fault source unexpectedly returned")
    remote = recv(pipe)
    name = agent.add_remote_agent(remote["metadata"])

    def make_handle(agent, size):
        return agent.initialize_xfer(
            "READ",
            agent.get_xfer_descs([(tensor.data_ptr(), size, 0)], "VRAM"),
            agent.get_xfer_descs([(remote["desc"][0][0], size, 0)], "VRAM"),
            name,
        )

    warm = make_handle(agent, 4)
    agent.transfer(warm)
    deadline = time.monotonic() + 30
    while agent.check_xfer_state(warm) == "PROC":
        if time.monotonic() > deadline:
            raise TimeoutError("Warmup stalled")
        time.sleep(0.001)
    if agent.check_xfer_state(warm) != "DONE":
        raise RuntimeError("Warmup failed")
    agent.release_xfer_handle(warm)
    torch.cuda.synchronize()
    assert tensor[0].item() == 42
    pipe.send("warmup-done")
    assert recv(pipe) == "peer-stopped"
    handle = make_handle(agent, tensor.numel() * 4)
    submit = agent.transfer(handle)
    state = agent.check_xfer_state(handle)
    # Required evidence: operation has actually been submitted and is pending.
    if state != "PROC":
        raise RuntimeError(
            f"Fault injection missed in-flight phase: {submit=}, {state=}"
        )
    pipe.send(dict(event="pending", submit=submit, state=state))
    assert recv(pipe) == "peer-killed"
    start = time.monotonic()
    deadline = start + 45
    while True:
        try:
            state = agent.check_xfer_state(handle)
        except Exception as exc:
            if type(exc).__name__ != "nixlRemoteDisconnectError":
                raise
            state = "ERR"
        if state != "PROC":
            break
        if time.monotonic() > deadline:
            raise TimeoutError("Native operation did not terminate after peer exit")
        time.sleep(0.01)
    if state != "ERR":
        raise RuntimeError(f"Expected native error after peer exit, got {state}")
    # Quiesce already-submitted device copies after the terminal native error.
    torch.cuda.synchronize()
    # Release only after terminal failure, then deregister actual device memory.
    agent.release_xfer_handle(handle)
    agent.remove_remote_agent(name)
    agent.deregister_memory(registered)
    result = dict(
        status="passed",
        pending_state="PROC",
        terminal_state=state,
        failure_seconds=time.monotonic() - start,
        handle_released=True,
        memory_deregistered=True,
    )
    (args.output / "consumer.json").write_text(json.dumps(result, indent=2) + "\n")
    del agent
    pipe.send("released")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["cuda", "metax"], required=True)
    parser.add_argument("--devices", default="3,3")
    parser.add_argument("--force-close", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.devices.split(",")) != 2:
        parser.error("provide two device ordinals")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["UCX_TLS"] = (
        "maca_copy,tcp" if args.platform == "metax" else "cuda_copy,tcp"
    )
    os.environ["UCX_PROTO_ENABLE"] = "y"
    context = mp.get_context("spawn")
    pipes = [context.Pipe() for _ in range(2)]
    children = [
        context.Process(target=worker, args=(i, pipes[i][1], args)) for i in range(2)
    ]
    summary = dict(
        status="failed",
        platform=args.platform,
        devices=args.devices,
        force_close=args.force_close,
        plugin_directory=os.environ.get("NIXL_PLUGIN_DIR"),
        ucx_tls=os.environ["UCX_TLS"],
        events=[],
    )
    try:
        for child in children:
            child.start()
        metadata = [recv(pair[0]) for pair in pipes]
        pipes[1][0].send(metadata[0])
        assert recv(pipes[1][0]) == "warmup-done"
        os.kill(children[0].pid, signal.SIGSTOP)
        pipes[1][0].send("peer-stopped")
        event = recv(pipes[1][0])
        if event.get("state") != "PROC":
            raise RuntimeError(f"Missing pending evidence: {event}")
        summary["events"].append(event)
        children[0].kill()
        children[0].join(10)
        pipes[1][0].send("peer-killed")
        assert recv(pipes[1][0]) == "released"
        children[1].join(30)
        if children[0].exitcode != -signal.SIGKILL or children[1].exitcode != 0:
            raise RuntimeError("Unexpected child exit")
        summary["status"] = "passed"
    except Exception as exc:
        summary["error"] = repr(exc)
        raise
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
            child.join()
        summary["exit_codes"] = [child.exitcode for child in children]
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
