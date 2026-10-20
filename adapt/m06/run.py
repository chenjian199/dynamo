#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded real-engine KVBM offload/reload acceptance in a vendor dev container."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psutil
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from adapt.m05.run import metric_total  # noqa: E402
from tests.utils.managed_process import ManagedProcess  # noqa: E402
from tests.utils.port_utils import allocate_port, deallocate_port  # noqa: E402


@contextmanager
def tracked_process(**kwargs):
    """Retain child identities before a failed engine reparents its workers."""
    process = ManagedProcess(**kwargs)
    stop = threading.Event()
    children = {}

    def track():
        while not stop.wait(0.2):
            if process.proc is None:
                continue
            try:
                for child in psutil.Process(process.proc.pid).children(recursive=True):
                    children[(child.pid, child.create_time())] = child
            except psutil.NoSuchProcess:
                pass

    tracker = threading.Thread(target=track, daemon=True)
    tracker.start()
    try:
        with process:
            yield process
    finally:
        stop.set()
        tracker.join(timeout=2)
        alive = []
        for child in children.values():
            try:
                if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                    alive.append(child)
            except psutil.NoSuchProcess:
                pass
        for child in alive:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, remaining = psutil.wait_procs(alive, timeout=5)
        for child in remaining:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(remaining, timeout=5)


def completion_text(row):
    return row["response"]["choices"][0]["text"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", required=True, choices=["cuda", "metax", "ascend"]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tp", type=int, choices=[1, 2], default=1)
    parser.add_argument("--devices", default="0")
    parser.add_argument("--model", default="/NFS_62/Models/Qwen_Series/Qwen3-0.6B")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--native-cuda", action="store_true")
    parser.add_argument("--disk", action="store_true")
    parser.add_argument("--direct-disk", action="store_true")
    parser.add_argument("--baseline-summary", type=Path)
    parser.add_argument("--cancel", action="store_true")
    parser.add_argument("--prompt-kind", choices=["text", "synthetic"], default="text")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--host-blocks", type=int)
    parser.add_argument("--allow-cache-miss", action="store_true")
    parser.add_argument("--disable-custom-all-reduce", action="store_true")
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error("cycles must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    out = args.output.resolve()
    (out / "disk").mkdir()
    root = Path(__file__).resolve().parents[2]
    sources = (root / "adapt/m06/sources.txt").read_text().splitlines()
    source = {
        "files": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sources
            if (root / name).is_file()
        },
        "python": sys.executable,
    }
    spec = importlib.util.find_spec("kvbm")
    if spec and spec.origin:
        core = Path(spec.origin).parent / "_core.abi3.so"
        if core.exists():
            source["kvbm_core_sha256"] = hashlib.sha256(core.read_bytes()).hexdigest()
    source["versions"] = {
        name: importlib.metadata.version(name) for name in ("torch", "vllm", "kvbm")
    }
    (out / "source.json").write_text(json.dumps(source, indent=2) + "\n")

    ports = [allocate_port(21000 + i * 100) for i in range(4)]
    http, pub, ack, metrics = ports
    disk_host_blocks = "12" if args.platform == "ascend" else "24"
    env = dict(
        os.environ,
        DYN_PLATFORM=args.platform,
        HF_HUB_OFFLINE="1",
        DYN_RUNTIME_ENABLED_KVBM="0",
        DYN_KVBM_VERIFY_TRANSFERS="1",
        DYN_LOG="info,dynamo_llm::block_manager::distributed::platform=debug",
        RUST_BACKTRACE="1",
        DYN_KVBM_METRICS="true",
        DYN_KVBM_DISABLE_DISK_OFFLOAD_FILTER="1" if args.disk else "0",
        DYN_KVBM_METRICS_PORT=str(metrics),
        DYN_KVBM_LEADER_ZMQ_PUB_PORT=str(pub),
        DYN_KVBM_LEADER_ZMQ_ACK_PORT=str(ack),
        DYN_KVBM_LEADER_WORKER_INIT_TIMEOUT_SECS="120",
        DYN_KVBM_CPU_CACHE_OVERRIDE_NUM_BLOCKS=(
            "0" if args.direct_disk else disk_host_blocks if args.disk else "128"
        ),
        DYN_KVBM_DISK_CACHE_OVERRIDE_NUM_BLOCKS="128"
        if (args.disk or args.direct_disk)
        else "0",
        DYN_KVBM_DISK_CACHE_DIR=str(out / "disk"),
        TOKENIZERS_PARALLELISM="false",
        PYTHONHASHSEED="0",
    )
    env[
        "ASCEND_RT_VISIBLE_DEVICES"
        if args.platform == "ascend"
        else "CUDA_VISIBLE_DEVICES"
    ] = args.devices
    if args.native_cuda:
        env["DYN_KVBM_TRANSFER_BACKEND"] = "platform"
    if args.platform == "cuda" and not args.baseline and not args.native_cuda:
        distribution = importlib.metadata.distribution("nixl-cu13")
        capi = [
            distribution.locate_file(f)
            for f in distribution.files
            if f.name == "libnixl_capi.so"
        ]
        if len(capi) != 1:
            raise RuntimeError("CUDA KVBM needs exactly one NIXL 1.3.2 C API library")
        env["LD_LIBRARY_PATH"] = (
            str(capi[0].parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        )
    if args.host_blocks is not None:
        env["DYN_KVBM_CPU_CACHE_OVERRIDE_NUM_BLOCKS"] = str(args.host_blocks)
    block_size = 128 if args.platform == "ascend" else 64
    command = [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        args.model,
        "--served-model-name",
        "m06-model",
        "--host",
        "127.0.0.1",
        "--port",
        str(http),
        "--enforce-eager",
        "--dtype",
        "bfloat16",
        "--tensor-parallel-size",
        str(args.tp),
        "--max-model-len",
        "2048",
        "--max-num-seqs",
        "2",
        "--enable-prefix-caching",
        "--block-size",
        str(block_size),
        "--num-gpu-blocks-override",
        str(2048 // block_size),
        "--gpu-memory-utilization",
        "0.15",
    ]
    if not args.baseline:
        command += [
            "--kv-transfer-config",
            json.dumps(
                dict(
                    kv_connector="DynamoConnector",
                    kv_role="kv_both",
                    kv_connector_module_path="kvbm.vllm_integration.connector",
                )
            ),
        ]
    if args.disable_custom_all_reduce:
        command.append("--disable-custom-all-reduce")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)

    def prompt(token):
        if args.prompt_kind == "synthetic":
            return [token] * 640
        context = tokenizer.encode(
            (f"Record {token}: Paris is the capital of France. " * 150),
            add_special_tokens=False,
        )
        ending = tokenizer.encode(
            "\nQuestion: What is the capital of France? Answer briefly.\nAnswer:",
            add_special_tokens=False,
        )
        return context[: 640 - len(ending)] + ending

    summary = {
        "status": "failed",
        "platform": args.platform,
        "configuration": {
            k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()
        },
        "command": command,
        "responses": [],
        "resources": [],
    }
    try:
        with tracked_process(
            command=command,
            env=env,
            log_dir=str(out),
            display_name="engine",
            health_check_urls=[f"http://127.0.0.1:{http}/health"],
            timeout=600,
            terminate_all_matching_process_names=False,
        ) as process:
            url = f"http://127.0.0.1:{http}"

            def metric(label):
                response = requests.get(url + "/metrics", timeout=10)
                response.raise_for_status()
                (out / f"{label}.metrics.txt").write_text(response.text)
                return metric_total(
                    response.text, "vllm:external_prefix_cache_hits_total"
                )

            before = metric("before")
            # Compare external-cache reload with an equivalent resident prefix
            # hit (request 1), not full uncached prefill (request 0): BF16 kernels
            # may differ numerically with the prefill split. Four distinct
            # prefixes exceed GPU capacity before the final repeat.
            for index, token in enumerate([42, 42, 43, 44, 45, 42] * args.cycles):
                start = time.monotonic()
                response = requests.post(
                    url + "/v1/completions",
                    timeout=180,
                    json=dict(
                        model="m06-model",
                        prompt=prompt(token),
                        max_tokens=32,
                        logprobs=5,
                        temperature=0,
                        seed=17,
                        ignore_eos=True,
                    ),
                )
                response.raise_for_status()
                result = response.json()
                summary["responses"].append(
                    dict(token=token, seconds=time.monotonic() - start, response=result)
                )
                metric(f"request-{index}")
                if index % 6 == 5:
                    parent = psutil.Process(process.proc.pid)
                    summary["resources"].append(
                        {
                            "request": index + 1,
                            "processes": [
                                {
                                    "pid": p.pid,
                                    "rss": p.memory_info().rss,
                                    "fds": p.num_fds(),
                                }
                                for p in [parent] + parent.children(recursive=True)
                                if p.is_running()
                            ],
                        }
                    )
                if args.disk:
                    time.sleep(8)  # Let asynchronous G2 -> G3 promotion finish.
            after = metric("after")
            summary["external_cache_hits"] = after - before
            first = summary["responses"][
                0 if (args.baseline or args.allow_cache_miss) else 1
            ]
            last = summary["responses"][-1]
            assert completion_text(first) == completion_text(last)
            expected_tokens = first["response"]["usage"]["completion_tokens"]
            assert last["response"]["usage"]["completion_tokens"] == expected_tokens
            if not args.baseline and not args.allow_cache_miss:
                assert after > before, "no external KV cache hit observed"
            if args.baseline_summary:
                baseline = json.loads(args.baseline_summary.read_text())
                assert baseline["status"] == "passed"
                references = list(baseline["responses"][:6])
                if not args.baseline and not args.allow_cache_miss:
                    references[-1] = references[1]  # resident hit, same prefill split
                for observed, reference in zip(
                    summary["responses"][:6], references, strict=True
                ):
                    expected_text = completion_text(reference)
                    assert (
                        completion_text(observed) == expected_text
                    ), "baseline output mismatch"
                summary["baseline_output_parity"] = True
            if args.cancel:
                for index in range(3):
                    with requests.post(
                        url + "/v1/completions",
                        timeout=180,
                        stream=True,
                        json=dict(
                            model="m06-model",
                            prompt=prompt(46 + index),
                            max_tokens=1000,
                            temperature=0,
                            ignore_eos=True,
                            stream=True,
                        ),
                    ) as stream:
                        stream.raise_for_status()
                        assert next(stream.iter_lines()), "empty streaming response"
                    time.sleep(1)
                recovery = requests.post(
                    url + "/v1/completions",
                    timeout=180,
                    json=dict(
                        model="m06-model",
                        prompt=prompt(42),
                        max_tokens=32,
                        logprobs=5,
                        temperature=0,
                        seed=17,
                        ignore_eos=True,
                    ),
                )
                recovery.raise_for_status()
                assert recovery.json()["choices"][0]["text"] == completion_text(first)
                summary["cancelled_streams"] = 3
                summary["post_cancel_output_parity"] = True
            if args.cycles >= 4:
                initial = {p["pid"]: p for p in summary["resources"][1]["processes"]}
                final = {p["pid"]: p for p in summary["resources"][-1]["processes"]}
                assert initial.keys() == final.keys(), "engine process set changed"
                for pid, last_sample in final.items():
                    assert (
                        last_sample["fds"] - initial[pid]["fds"] <= 16
                    ), "unbounded file descriptor growth"
                    assert (
                        last_sample["rss"] - initial[pid]["rss"] <= 128 * 1024**2
                    ), "RSS growth exceeds 128 MiB after warmup"
                summary["bounded_resources"] = True
            summary["status"] = "passed"
    finally:
        log_path = out / "engine.log.txt"
        if log_path.exists():
            log = re.sub(r"\x1b\[[0-9;]*m", "", log_path.read_text(errors="replace"))
            summary["reload_byte_checks"] = log.count(
                "platform KVBM reload byte verification passed"
            )
            directions = re.findall(
                r"platform KVBM transfer completed source=(\w+) target=(\w+)", log
            )
            summary["transfer_directions"] = sorted(
                {f"{a}->{b}" for a, b in directions}
            )
            native_transfer = args.native_cuda or args.platform != "cuda"
            successful = summary["status"] == "passed" and not args.baseline
            if successful and native_transfer:
                if not args.allow_cache_miss and not summary["reload_byte_checks"]:
                    summary.update(
                        status="failed",
                        error="missing engine KV reload byte verification",
                    )
                required = (
                    {"Host->Disk", "Disk->Device"}
                    if args.disk
                    else {"Device->Disk", "Disk->Device"}
                    if args.direct_disk
                    else set()
                )
                if not required.issubset(summary["transfer_directions"]):
                    summary.update(
                        status="failed",
                        error="required disk tier transfer was not observed",
                    )
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        for port in ports:
            deallocate_port(port)
    if summary["status"] != "passed":
        raise RuntimeError(summary.get("error", "M06 acceptance failed"))


if __name__ == "__main__":
    main()
