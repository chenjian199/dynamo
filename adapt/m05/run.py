#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run a bounded single-host PD smoke test in the existing vendor dev container."""

import argparse
import contextlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.utils.managed_process import ManagedProcess
from tests.utils.port_utils import allocate_port, deallocate_port


def metric_total(text, name):
    return sum(
        float(line.split()[-1])
        for line in text.splitlines()
        if line.startswith(name + "{")
    )


def collect_idle_metrics(output, ports, roles, suffix):
    evidence = {}
    deadline = time.monotonic() + 60
    while True:
        idle = True
        for index, role in enumerate(roles):
            response = requests.get(
                f"http://127.0.0.1:{ports[index]}/metrics", timeout=5
            )
            response.raise_for_status()
            text = response.text
            (output / f"{role}.{suffix}.metrics.txt").write_text(text)
            names = [
                "vllm:num_requests_running",
                "vllm:num_requests_waiting",
                "vllm:kv_cache_usage_perc",
                "vllm:external_prefix_cache_hits_total",
            ]
            values = {name: metric_total(text, name) for name in names}
            if any(name + "{" not in text for name in names[:3]):
                raise RuntimeError(f"Missing idle metrics for {role}")
            idle = idle and all(values[name] == 0 for name in names[:3])
            evidence[role] = values
        if idle:
            return evidence
        if time.monotonic() > deadline:
            raise RuntimeError(f"KV/request occupancy did not drain: {evidence}")
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", choices=["cuda", "metax", "ascend"], required=True
    )
    parser.add_argument("--devices", default="0,1")
    parser.add_argument("--model", default="/NFS_62/Models/Qwen_Series/Qwen3-0.6B")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("--prefill-workers", type=int, default=1)
    parser.add_argument("--decode-workers", type=int, default=1)
    parser.add_argument("--allow-shared-devices", action="store_true")
    parser.add_argument("--cancel", action="store_true")
    parser.add_argument("--handoff-case", choices=["mismatch", "unreachable"])
    parser.add_argument("--fault", choices=["producer-exit", "consumer-exit"])
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--connector", choices=["default", "nixl"], default="default")
    args = parser.parse_args()
    if args.connector == "nixl" and args.platform == "ascend":
        parser.error("Ascend NIXL is not supported by this runtime")
    devices = args.devices.split(",")
    if min(args.prefill_workers, args.decode_workers) < 1:
        parser.error("worker counts must be positive")
    multi = args.prefill_workers != 1 or args.decode_workers != 1
    if multi and (args.aggregate or args.fault):
        parser.error("multi-worker topology cannot combine with aggregate/fault")
    roles = (
        ["aggregate"]
        if args.aggregate
        else (
            ["decode", "prefill"]
            if not multi
            else [f"decode-{i}" for i in range(args.decode_workers)]
            + [f"prefill-{i}" for i in range(args.prefill_workers)]
        )
    )
    count = len(roles)
    if not all(item.isdigit() for item in devices) or len(devices) < count:
        parser.error("--devices must provide a non-negative ordinal for each worker")
    if len(set(devices)) != len(devices) and not args.allow_shared_devices:
        parser.error("duplicate devices require --allow-shared-devices")
    args.output.mkdir(parents=True, exist_ok=True)
    namespace = "m05-" + uuid.uuid4().hex[:10]
    env = dict(
        os.environ,
        DYN_NAMESPACE=namespace,
        DYN_DISCOVERY_BACKEND="file",
        DYN_FILE_KV="/tmp/" + namespace,
        DYN_REQUEST_PLANE="tcp",
        DYN_EVENT_PLANE="zmq",
        HF_HUB_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        PYTHONHASHSEED="0",
        VLLM_LOGGING_LEVEL="DEBUG",
        VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT="60",
        ASCEND_TRANSFER_TIMEOUT="30000",
        DYN_HTTP_PORT="18200",
        DYN_SYSTEM_PORT="18201",
    )
    env.pop("DYN_PLATFORM", None)
    command = [
        sys.executable,
        "-m",
        "dynamo.vllm",
        "--model",
        args.model,
        "--served-model-name",
        "m05-model",
        "--enforce-eager",
        "--gpu-memory-utilization",
        "0.2",
        "--max-model-len",
        "2048",
        "--max-num-seqs",
        "4",
        "--kv-cache-memory-bytes",
        "536870912",
        "--block-size",
        "128" if args.platform == "ascend" else "64",
        "--no-enable-prefix-caching",
    ]
    ports = [allocate_port(19000 + i * 100) for i in range(2 + 4 * count)]
    http_port, frontend_system, *worker_ports = ports
    env.update(DYN_HTTP_PORT=str(http_port), DYN_SYSTEM_PORT=str(frontend_system))
    commands = {}
    workers = {}
    summary = {
        "platform": args.platform,
        "aggregate": args.aggregate,
        "status": "failed",
        "configuration": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
    }
    try:
        packages = ["vllm", "torch"]
        if args.platform in ("metax", "ascend"):
            packages.append("vllm-" + args.platform)
            check = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("patch_vendor.py")),
                    "--platform",
                    args.platform,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            summary["vendor_connector"] = json.loads(check.stdout)
        summary["software"] = {
            name: importlib.metadata.version(name) for name in packages
        }
        with contextlib.ExitStack() as stack:
            frontend = ManagedProcess(
                command=[sys.executable, "-m", "dynamo.frontend"],
                env=dict(env, DYN_PLATFORM="cpu"),
                log_dir=str(args.output.resolve()),
                display_name="frontend",
                terminate_all_matching_process_names=False,
            )
            stack.enter_context(frontend)
            for index, label in enumerate(roles):
                role = label.split("-")[0]
                worker_env = dict(
                    env,
                    DYN_SYSTEM_PORT=str(worker_ports[index]),
                    VLLM_NIXL_SIDE_CHANNEL_PORT=str(worker_ports[count + index]),
                    DYN_FORWARDPASS_METRIC_PORT=str(worker_ports[2 * count + index]),
                    VLLM_MOONCAKE_BOOTSTRAP_PORT=str(worker_ports[3 * count + index]),
                )
                worker_env[
                    "ASCEND_RT_VISIBLE_DEVICES"
                    if args.platform == "ascend"
                    else "CUDA_VISIBLE_DEVICES"
                ] = devices[index]
                if not args.legacy:
                    worker_env["DYN_PLATFORM"] = args.platform
                argv = command.copy()
                if role != "aggregate":
                    kv = {"kv_connector": "NixlConnector", "kv_role": "kv_both"}
                    if args.platform == "metax" and args.connector != "nixl":
                        kv = {
                            "kv_connector": "MooncakeConnector",
                            "kv_role": "kv_producer"
                            if role == "prefill"
                            else "kv_consumer",
                            "kv_connector_extra_config": {"mooncake_protocol": "maca"},
                        }
                    if args.platform == "ascend":
                        kv = {
                            "kv_connector": "MooncakeConnector",
                            "kv_connector_module_path": "vllm_ascend.distributed.kv_transfer.kv_p2p.mooncake_connector",
                            "kv_role": "kv_producer"
                            if role == "prefill"
                            else "kv_consumer",
                            "kv_port": worker_ports[3 * count + index],
                            "kv_connector_extra_config": {
                                "prefill": {"tp_size": 1, "dp_size": 1},
                                "decode": {"tp_size": 1, "dp_size": 1},
                            },
                        }
                    argv += [
                        "--disaggregation-mode",
                        role,
                        "--kv-transfer-config",
                        json.dumps(kv),
                    ]
                commands[label] = {
                    "argv": argv,
                    "device": devices[index],
                    "system_port": worker_ports[index],
                }
                process = ManagedProcess(
                    command=argv,
                    env=worker_env,
                    log_dir=str(args.output.resolve()),
                    display_name=label,
                    terminate_all_matching_process_names=False,
                    timeout=480,
                    health_check_urls=[
                        f"http://127.0.0.1:{worker_ports[index]}/health"
                    ],
                )
                stack.enter_context(process)
                workers[label] = process
            (args.output / "commands.json").write_text(
                json.dumps(commands, indent=2) + "\n"
            )
            deadline = time.monotonic() + 60
            while True:
                response = requests.get(
                    f"http://127.0.0.1:{http_port}/v1/models", timeout=5
                )
                if response.ok and response.json().get("data"):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("model did not register")
                time.sleep(1)
            prompt = "Explain why the sky is blue. " * 80

            def complete(_):
                response = requests.post(
                    f"http://127.0.0.1:{http_port}/v1/completions",
                    json={
                        "model": "m05-model",
                        "prompt": prompt,
                        "max_tokens": 32,
                        "temperature": 0,
                        "seed": 42,
                    },
                    timeout=120,
                )
                response.raise_for_status()
                return response.json()

            if multi:
                report = args.output.resolve() / "topology.json"
                with ManagedProcess(
                    command=[
                        sys.executable,
                        str(Path(__file__).with_name("topology.py")),
                        "--namespace",
                        namespace,
                        "--prefill-workers",
                        str(args.prefill_workers),
                        "--decode-workers",
                        str(args.decode_workers),
                        "--output",
                        str(report),
                    ],
                    env=dict(env, DYN_PLATFORM="cpu", DYN_SYSTEM_PORT="0"),
                    display_name="topology",
                    log_dir=str(args.output.resolve()),
                    terminate_all_matching_process_names=False,
                ) as client:
                    code = client.proc.wait(timeout=240)
                summary["topology"] = json.loads(report.read_text())
                if code or summary["topology"]["status"] != "passed":
                    raise RuntimeError("Direct P-to-D pair coverage failed")
                with ThreadPoolExecutor(max_workers=6) as pool:
                    outputs = list(pool.map(complete, range(12)))
            else:
                outputs = [complete(i) for i in range(3)]
            if any(item["choices"] != outputs[0]["choices"] for item in outputs):
                raise RuntimeError("Deterministic completions differ across workers")
            summary.update(responses=outputs)
            time.sleep(6)  # Wait for the engine's metrics publication interval.
            summary["idle"] = collect_idle_metrics(
                args.output, worker_ports, roles, "normal"
            )
            if not args.aggregate:
                for label in roles:
                    if label.startswith("decode"):
                        if (
                            summary["idle"][label][
                                "vllm:external_prefix_cache_hits_total"
                            ]
                            <= 0
                        ):
                            raise RuntimeError(f"No remote KV hits observed on {label}")
                    elif (
                        "Finished sending KV transfer" not in workers[label].read_logs()
                    ):
                        raise RuntimeError(
                            f"No producer KV completion evidence on {label}"
                        )
            if args.handoff_case:
                unused_port = allocate_port(21000)
                try:
                    report = args.output.resolve() / "handoff.json"
                    with ManagedProcess(
                        command=[
                            sys.executable,
                            str(Path(__file__).with_name("handoff.py")),
                            "--namespace",
                            namespace,
                            "--case",
                            args.handoff_case,
                            "--unreachable-port",
                            str(unused_port),
                            "--output",
                            str(report),
                        ],
                        env=dict(env, DYN_PLATFORM="cpu", DYN_SYSTEM_PORT="0"),
                        display_name="handoff",
                        log_dir=str(args.output.resolve()),
                        terminate_all_matching_process_names=False,
                    ) as client:
                        code = client.proc.wait(timeout=120)
                    summary["handoff"] = json.loads(report.read_text())
                    if code or summary["handoff"]["status"] == "failed":
                        raise RuntimeError(
                            "Handoff fault did not complete within backend deadline"
                        )
                    time.sleep(
                        35
                    )  # Producer lease is 30s; do not release blocks in Dynamo.
                    summary["handoff_idle"] = collect_idle_metrics(
                        args.output, worker_ports, roles, "handoff"
                    )
                    recovery = requests.post(
                        f"http://127.0.0.1:{http_port}/v1/completions",
                        json={
                            "model": "m05-model",
                            "prompt": prompt,
                            "max_tokens": 32,
                            "temperature": 0,
                            "seed": 42,
                        },
                        timeout=60,
                    )
                    recovery.raise_for_status()
                    if recovery.json()["choices"] != outputs[0]["choices"]:
                        raise RuntimeError("Output changed after handoff fault")
                    summary["handoff_recovery"] = "passed"
                finally:
                    deallocate_port(unused_port)
            if args.cancel:
                with requests.post(
                    f"http://127.0.0.1:{http_port}/v1/completions",
                    json={
                        "model": "m05-model",
                        "prompt": prompt,
                        "max_tokens": 1024,
                        "ignore_eos": True,
                        "temperature": 0,
                        "stream": True,
                    },
                    stream=True,
                    timeout=60,
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines(chunk_size=1):
                        if line.startswith(b"data: ") and b'"text":"' in line.replace(
                            b" ", b""
                        ):
                            break
                    else:
                        raise RuntimeError("Stream ended before cancellation")
                time.sleep(6)
                summary["cancel_idle"] = collect_idle_metrics(
                    args.output, worker_ports, roles, "cancel"
                )
                recovery = requests.post(
                    f"http://127.0.0.1:{http_port}/v1/completions",
                    json={
                        "model": "m05-model",
                        "prompt": prompt,
                        "max_tokens": 32,
                        "temperature": 0,
                        "seed": 42,
                    },
                    timeout=60,
                )
                recovery.raise_for_status()
                if recovery.json()["choices"] != outputs[0]["choices"]:
                    raise RuntimeError("Output changed after cancellation")
                summary["cancel_recovery"] = "passed"
            if args.fault:
                role = "prefill" if args.fault == "producer-exit" else "decode"
                children = psutil.Process(workers[role].proc.pid).children(
                    recursive=True
                )
                engines = [child for child in children if "EngineCore" in child.name()]
                if len(engines) != 1:
                    raise RuntimeError(
                        "Expected exactly one owned engine for fault injection"
                    )
                with requests.post(
                    f"http://127.0.0.1:{http_port}/v1/completions",
                    json={
                        "model": "m05-model",
                        "prompt": prompt,
                        "max_tokens": 1024,
                        "ignore_eos": True,
                        "temperature": 0,
                        "stream": True,
                    },
                    stream=True,
                    timeout=60,
                ) as response:
                    response.raise_for_status()
                    iterator = response.iter_lines(chunk_size=1)
                    for line in iterator:
                        if line.startswith(b"data: "):
                            engines[0].send_signal(signal.SIGKILL)
                            break
                    remainder = b"\n".join(iterator).decode()
                summary["fault"] = {
                    "case": args.fault,
                    "phase": "after first SSE event",
                    "stream_tail": remainder[-2000:],
                }
                if args.fault == "consumer-exit" and not any(
                    word in remainder for word in ("error", "Error")
                ):
                    raise RuntimeError("Consumer exit did not surface a stream error")
                if (
                    args.fault == "producer-exit"
                    and '"finish_reason":"length"' not in remainder.replace(" ", "")
                ):
                    raise RuntimeError(
                        "Decode did not survive producer exit after handoff"
                    )
            for index, role in enumerate(roles if not args.fault else []):
                response = requests.get(
                    f"http://127.0.0.1:{worker_ports[index]}/metrics", timeout=10
                )
                response.raise_for_status()
                (args.output / (role + ".metrics.txt")).write_text(response.text)
        summary["status"] = "passed"
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        for port in ports:
            deallocate_port(port)
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
