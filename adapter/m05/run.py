#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run a bounded single-host PD smoke test in the existing vendor dev container."""

import argparse
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
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

from tests.utils.managed_process import ManagedProcess  # noqa: E402
from tests.utils.port_utils import allocate_port, deallocate_port  # noqa: E402


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
    parser.add_argument("--kv-routing", action="store_true")
    parser.add_argument("--kv-routing-tier", choices=("host", "disk"), default="host")
    parser.add_argument(
        "--kvbm", action="store_true", help="M06: add local KVBM to prefill/aggregate"
    )
    parser.add_argument("--prefill-workers", type=int, default=1)
    parser.add_argument("--decode-workers", type=int, default=1)
    parser.add_argument("--prefill-tp", type=int, choices=[1, 2], default=1)
    parser.add_argument("--decode-tp", type=int, choices=[1, 2], default=1)
    parser.add_argument("--allow-shared-devices", action="store_true")
    parser.add_argument("--cancel", action="store_true")
    parser.add_argument("--handoff-case", choices=["mismatch", "unreachable"])
    parser.add_argument("--fault", choices=["producer-exit", "consumer-exit"])
    parser.add_argument("--inflight-fault", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--connector", choices=["default", "nixl"], default="default")
    parser.add_argument("--soak-seconds", type=int, default=0)
    parser.add_argument("--soak-requests", type=int, default=1000)
    args = parser.parse_args()
    if args.soak_seconds < 0 or args.soak_requests < 96:
        parser.error("soak requires nonnegative seconds and at least 96 requests")
    if args.connector == "nixl" and args.platform == "ascend":
        parser.error("Ascend NIXL is not supported by this runtime")
    if args.inflight_fault and (
        args.prefill_tp != 1
        or args.decode_tp != 1
        or args.aggregate
        or args.soak_seconds
        or args.fault
    ):
        parser.error(
            "in-flight process failure currently requires TP1, separate from soak"
        )
    devices = args.devices.split(",")
    if min(args.prefill_workers, args.decode_workers) < 1:
        parser.error("worker counts must be positive")
    multi = args.prefill_workers != 1 or args.decode_workers != 1
    if args.kv_routing_tier == "disk" and not args.kv_routing:
        parser.error("--kv-routing-tier=disk requires --kv-routing")
    if args.kv_routing and (
        not args.aggregate
        or not args.kvbm
        or multi
        or args.decode_tp != 1
        or args.soak_seconds
        or args.cancel
        or args.handoff_case
        or args.fault
        or args.inflight_fault
    ):
        parser.error("--kv-routing requires aggregate KVBM with two TP1 workers")
    if args.soak_seconds and (args.aggregate or args.fault or multi):
        parser.error("soak currently requires one P and one D without exit injection")
    if multi and (args.aggregate or args.fault or args.inflight_fault):
        parser.error("multi-worker topology cannot combine with aggregate/fault")
    roles = (
        (["aggregate-0", "aggregate-1"] if args.kv_routing else ["aggregate"])
        if args.aggregate
        else (
            ["decode", "prefill"]
            if not multi
            else [f"decode-{i}" for i in range(args.decode_workers)]
            + [f"prefill-{i}" for i in range(args.prefill_workers)]
        )
    )
    count = len(roles)

    def valid_device(item):
        return item.isdigit() or (
            args.platform == "cuda"
            and re.fullmatch(
                r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", item
            )
        )

    if not all(valid_device(item) for item in devices) or len(devices) < (
        args.decode_tp * count
        if args.aggregate
        else args.decode_workers * args.decode_tp
        + args.prefill_workers * args.prefill_tp
    ):
        parser.error("--devices requires non-negative ordinals (or CUDA GPU UUIDs)")
    if len(set(devices)) != len(devices) and not args.allow_shared_devices:
        parser.error("duplicate devices require --allow-shared-devices")
    groups = []
    offset = 0
    for label in roles:
        tp = args.prefill_tp if label.startswith("prefill") else args.decode_tp
        groups.append(",".join(devices[offset : offset + tp]))
        offset += tp
    args.output.mkdir(parents=True, exist_ok=False)
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
        PYTHONPATH=str(Path(__file__).resolve().parents[2])
        + os.pathsep
        + os.environ.get("PYTHONPATH", ""),
        VLLM_LOGGING_LEVEL="DEBUG",
        VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT="60",
        ASCEND_TRANSFER_TIMEOUT="30000",
        DYN_HTTP_PORT="18200",
        DYN_SYSTEM_PORT="18201",
    )
    env.pop("DYN_PLATFORM", None)
    if args.kv_routing:
        env.update(
            DYN_ROUTER_USE_KV_EVENTS="true",
            DYN_KVBM_KV_EVENTS_ENABLE_CONSOLIDATOR="true",
            DYN_KVBM_KV_EVENTS_CONSOLIDATOR_MODE="passthrough",
        )
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
    if args.kvbm:
        command[-1] = "--enable-prefix-caching"
        # DynamoConnector disables HMA on prefill. NIXL includes this setting
        # in its compatibility hash, so configure the matching decode explicitly.
        command.append("--disable-hybrid-kv-cache-manager")
        env.update(
            DYN_RUNTIME_ENABLED_KVBM="0",
            DYN_KVBM_TRANSFER_BACKEND="platform",
            DYN_KVBM_CPU_CACHE_OVERRIDE_NUM_BLOCKS="128",
            DYN_KVBM_DISK_CACHE_OVERRIDE_NUM_BLOCKS="0",
            DYN_KVBM_VERIFY_TRANSFERS="1",
            DYN_LOG=os.environ.get(
                "DYN_LOG", "info,dynamo_llm::block_manager::distributed::platform=debug"
            ),
        )
        root = Path(__file__).resolve().parents[2]
        sources = (root / "adapter/m06/sources.txt").read_text().splitlines()
        kvbm_spec = importlib.util.find_spec("kvbm")
        if kvbm_spec is None or kvbm_spec.origin is None:
            raise RuntimeError("KVBM package is not installed")
        kvbm_core = Path(kvbm_spec.origin).parent / "_core.abi3.so"
        (args.output / "source.json").write_text(
            json.dumps(
                {
                    "python": sys.executable,
                    "versions": {
                        name: importlib.metadata.version(name)
                        for name in ("torch", "vllm", "kvbm")
                    },
                    "kvbm_core_sha256": hashlib.sha256(
                        kvbm_core.read_bytes()
                    ).hexdigest(),
                    "files": {
                        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in sources
                        if (root / name).is_file()
                    },
                },
                indent=2,
            )
            + "\n"
        )
    ports = [allocate_port(19000 + i * 100) for i in range(2 + 3 * count)]
    http_port, frontend_system, *worker_ports = ports
    worker_ports.extend([0] * count)
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
        summary["runtime_environment"] = {
            name: env.get(name)
            for name in ("SPT_NOENV", "GOMAXPROCS", "MALLOC_ARENA_MAX", "LD_PRELOAD")
        }
        with contextlib.ExitStack() as stack:
            frontend = ManagedProcess(
                command=[sys.executable, "-m", "dynamo.frontend"]
                + (["--router-mode", "kv"] if args.kv_routing else []),
                env=dict(env, DYN_PLATFORM="cpu"),
                log_dir=str(args.output.resolve()),
                display_name="frontend",
                terminate_all_matching_process_names=False,
            )
            stack.enter_context(frontend)
            for index, label in enumerate(roles):
                role = label.split("-")[0]
                # Allocate after earlier engines have started: Mooncake/ADXL
                # chooses RPC ports outside the test allocator's registry and
                # can otherwise take a later worker's not-yet-bound port.
                bootstrap_port = allocate_port(26000 + index * 100)
                ports.append(bootstrap_port)
                worker_ports[3 * count + index] = bootstrap_port
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
                ] = groups[index]
                if args.platform == "metax":
                    worker_env["MACA_VISIBLE_DEVICES"] = groups[index]
                if not args.legacy:
                    worker_env["DYN_PLATFORM"] = args.platform
                tp = args.prefill_tp if role == "prefill" else args.decode_tp
                argv = command + ["--tensor-parallel-size", str(tp)]
                if args.kv_routing:
                    event_port = allocate_port(29000 + index * 100)
                    ports.append(event_port)
                    argv += [
                        "--kv-events-config",
                        json.dumps(
                            dict(
                                publisher="zmq",
                                topic="kv-events",
                                endpoint=f"tcp://*:{event_port}",
                                enable_kv_cache_events=True,
                            )
                        ),
                    ]
                if args.kvbm and role in ("prefill", "aggregate"):
                    kvbm_ports = [
                        allocate_port(24000 + offset * 100) for offset in range(3)
                    ]
                    ports.extend(kvbm_ports)
                    worker_env.update(
                        DYN_KVBM_LEADER_ZMQ_PUB_PORT=str(kvbm_ports[0]),
                        DYN_KVBM_LEADER_ZMQ_ACK_PORT=str(kvbm_ports[1]),
                        DYN_KVBM_METRICS_PORT=str(kvbm_ports[2]),
                    )
                    if args.kv_routing_tier == "disk":
                        disk_dir = args.output.resolve() / "disk" / label
                        disk_dir.mkdir(parents=True)
                        worker_env.update(
                            DYN_KVBM_CPU_CACHE_OVERRIDE_NUM_BLOCKS=(
                                "12" if args.platform == "ascend" else "24"
                            ),
                            DYN_KVBM_DISK_CACHE_OVERRIDE_NUM_BLOCKS="128",
                            DYN_KVBM_DISABLE_DISK_OFFLOAD_FILTER="1",
                            DYN_KVBM_DISK_CACHE_DIR=str(disk_dir),
                        )
                    argv += [
                        "--num-gpu-blocks-override",
                        "16" if args.platform == "ascend" else "32",
                    ]
                    offload = {
                        "kv_connector": "DynamoConnector",
                        "kv_role": "kv_both",
                        "kv_connector_module_path": "kvbm.vllm_integration.connector",
                    }
                    if role == "aggregate":
                        argv += ["--kv-transfer-config", json.dumps(offload)]
                if args.soak_seconds or args.inflight_fault:
                    worker_env.update(
                        M05_OBSERVER_DIR=str(
                            args.output.resolve() / "observer" / label
                        ),
                        M05_OBSERVER_CONNECTOR="nixl"
                        if args.platform == "cuda" or args.connector == "nixl"
                        else "mooncake",
                    )
                    argv += [
                        "--worker-extension-cls",
                        "adapter.m05.observer.TransferObserver",
                    ]
                if args.inflight_fault:
                    worker_env["M05_INFLIGHT_DIR"] = str(
                        args.output.resolve() / "inflight"
                    )
                    if args.platform == "cuda" or args.connector == "nixl":
                        worker_env["UCX_TLS"] = (
                            "maca_copy,tcp"
                            if args.platform == "metax"
                            else "cuda_copy,tcp"
                        )
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
                                "heyi_tp_split": args.prefill_tp == 1
                                and args.decode_tp == 2,
                                "prefill": {"tp_size": args.prefill_tp, "dp_size": 1},
                                "decode": {"tp_size": args.decode_tp, "dp_size": 1},
                            },
                        }
                    if args.kvbm and role == "prefill":
                        kv = {
                            "kv_connector": "PdConnector",
                            "kv_role": "kv_both",
                            "kv_connector_module_path": "kvbm.vllm_integration.connector",
                            "kv_connector_extra_config": {
                                "connectors": [
                                    offload,
                                    kv,
                                ]
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
                    "device": groups[index],
                    "system_port": worker_ports[index],
                }
                (args.output / "commands.json").write_text(
                    json.dumps(commands, indent=2) + "\n"
                )
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
            if args.kv_routing:
                report = args.output.resolve() / "routing.json"
                with ManagedProcess(
                    command=[
                        sys.executable,
                        str(Path(__file__).parents[1] / "m06" / "routing.py"),
                        "--namespace",
                        namespace,
                        "--http-port",
                        str(http_port),
                        "--worker-ports",
                        ",".join(str(p) for p in worker_ports[:count]),
                        "--block-size",
                        "128" if args.platform == "ascend" else "64",
                        "--tier",
                        args.kv_routing_tier,
                        "--offload-log",
                        str(args.output.resolve() / "aggregate-0.log.txt"),
                        "--output",
                        str(report),
                    ],
                    env=dict(env, DYN_PLATFORM="cpu", DYN_SYSTEM_PORT="0"),
                    log_dir=str(args.output.resolve()),
                    display_name="routing",
                    terminate_all_matching_process_names=False,
                ) as client:
                    code = client.proc.wait(timeout=300)
                summary["routing"] = json.loads(report.read_text())
                if code or summary["routing"]["status"] != "passed":
                    raise RuntimeError("KVBM tier-aware routing failed")
                local_logs = re.sub(
                    r"\x1b\[[0-9;]*m",
                    "",
                    "\n".join(process.read_logs() for process in workers.values()),
                )
                checks = local_logs.count(
                    "platform KVBM reload byte verification passed"
                )
                if checks == 0:
                    raise RuntimeError("Routing did not trigger a verified KVBM reload")
                if args.kv_routing_tier == "disk" and not all(
                    direction in local_logs
                    for direction in (
                        "source=Host target=Disk",
                        "source=Disk target=Device",
                    )
                ):
                    raise RuntimeError("Missing disk offload/reload during routing")
                summary["kvbm_reload_byte_checks"] = checks
                summary["status"] = "passed"
                return
            prompt = "Explain why the sky is blue. " * 80

            def complete(_, text=None):
                response = requests.post(
                    f"http://127.0.0.1:{http_port}/v1/completions",
                    json={
                        "model": "m05-model",
                        "prompt": prompt if text is None else text,
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
            if args.kvbm:
                # Evict the original device prefix while retaining its G2 copy.
                # Compare reload against the equivalent resident-cache request;
                # full and cached BF16 prefill can choose different tied tokens.
                evictions = []
                for index in range(4):
                    evictions.append(complete(index, f"Case {index}: " + prompt))
                    time.sleep(8)
                reload = complete(0)
                summary["kvbm_evictions"] = evictions
                summary["kvbm_reload"] = reload
                if reload["choices"] != outputs[-1]["choices"]:
                    raise RuntimeError("KVBM reload differs from resident prefix")
                local_logs = "\n".join(
                    process.read_logs()
                    for label, process in workers.items()
                    if label.startswith(("prefill", "aggregate"))
                )
                checks = local_logs.count(
                    "platform KVBM reload byte verification passed"
                )
                summary["kvbm_reload_byte_checks"] = checks
                if checks == 0:
                    raise RuntimeError("No KVBM reload byte verification observed")
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
            if args.soak_seconds:
                from soak import run_soak

                def soak_fault():
                    unused_port = allocate_port(21000)
                    report = args.output.resolve() / (
                        "soak-handoff-" + uuid.uuid4().hex[:8] + ".json"
                    )
                    try:
                        with ManagedProcess(
                            command=[
                                sys.executable,
                                str(Path(__file__).with_name("handoff.py")),
                                "--namespace",
                                namespace,
                                "--case",
                                "unreachable",
                                "--unreachable-port",
                                str(unused_port),
                                "--output",
                                str(report),
                            ],
                            env=dict(env, DYN_PLATFORM="cpu", DYN_SYSTEM_PORT="0"),
                            display_name=report.stem,
                            log_dir=str(args.output.resolve()),
                            terminate_all_matching_process_names=False,
                        ) as client:
                            code = client.proc.wait(timeout=120)
                        if code or json.loads(report.read_text())["status"] == "failed":
                            raise RuntimeError("Soak fault failed to terminate")
                        time.sleep(35)
                    finally:
                        deallocate_port(unused_port)

                summary["soak"] = run_soak(
                    args,
                    workers,
                    http_port,
                    complete,
                    outputs[0]["choices"],
                    lambda suffix: collect_idle_metrics(
                        args.output, worker_ports, roles, suffix
                    ),
                    soak_fault,
                )
            if args.inflight_fault:
                from inflight_engine import run as run_inflight

                summary["inflight_fault"] = run_inflight(
                    args,
                    workers,
                    complete,
                    lambda role: collect_idle_metrics(
                        args.output,
                        [worker_ports[roles.index(role)]],
                        [role],
                        "inflight-survivor",
                    ),
                )
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
            for index, role in enumerate(
                roles if not (args.fault or args.inflight_fault) else []
            ):
                response = requests.get(
                    f"http://127.0.0.1:{worker_ports[index]}/metrics", timeout=10
                )
                response.raise_for_status()
                (args.output / (role + ".metrics.txt")).write_text(response.text)
        summary["status"] = "passed"
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        for port in ports:
            deallocate_port(port)
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
