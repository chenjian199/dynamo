# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded PD soak with post-warmup native/process resource checks."""

import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import requests


def resources(workers, output):
    processes = {}
    for role, worker in workers.items():
        root = psutil.Process(worker.proc.pid)
        for process in [root, *root.children(recursive=True)]:
            with process.oneshot():
                processes[str(process.pid)] = dict(
                    role=role,
                    name=process.name(),
                    rss=process.memory_info().rss,
                    fds=process.num_fds(),
                    threads=process.num_threads(),
                    thread_names=dict(
                        Counter(
                            Path(f"/proc/{process.pid}/task/{thread.id}/comm")
                            .read_text()
                            .strip()
                            for thread in process.threads()
                        )
                    ),
                )
    native = {}
    for path in (output / "observer").glob("*/native-*.json"):
        native[str(path.relative_to(output))] = json.loads(path.read_text())
    if not native:
        raise RuntimeError("Native observer produced no evidence")
    for item in native.values():
        for field in ("transfers", "sync_calls"):
            if item["active"].get(field, 0):
                raise RuntimeError(f"Native operations remain active: {item}")
    return dict(processes=processes, native=native)


def cancel(url, prompt):
    with requests.post(
        url,
        json=dict(
            model="m05-model",
            prompt=prompt,
            max_tokens=1024,
            ignore_eos=True,
            temperature=0,
            stream=True,
        ),
        stream=True,
        timeout=60,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=1):
            if line.startswith(b"data: ") and b'"text"' in line:
                return
    raise RuntimeError("No SSE event before cancellation")


def _run_soak(args, workers, http_port, complete, reference, idle, fault):
    output = args.output
    start = time.monotonic()
    report = dict(
        status="running",
        seconds=args.soak_seconds,
        minimum_requests=args.soak_requests,
        completed=0,
        cancellations=0,
        faults=0,
        samples=[],
        limits=dict(rss_growth_bytes=256 * 1024**2, fd_growth=8, thread_growth=4),
    )
    target = output / "soak.json"
    url = f"http://127.0.0.1:{http_port}/v1/completions"
    # Warmup includes concurrency and cancellation before the baseline sample.
    with ThreadPoolExecutor(max_workers=4) as pool:
        while (
            time.monotonic() - start < args.soak_seconds
            or report["completed"] < args.soak_requests
        ):
            outputs = list(pool.map(complete, range(32)))
            if any(item["choices"] != reference for item in outputs):
                raise RuntimeError("Soak output changed")
            report["completed"] += len(outputs)
            cancel(url, "Explain why the sky is blue. " * 80)
            report["cancellations"] += 1
            # Actual bad handoff and recovery, including during the steady phase.
            if report["completed"] in (320, 640, 960):
                fault()
                report["faults"] += 1
                if complete(0)["choices"] != reference:
                    raise RuntimeError("Soak recovery output changed")
            time.sleep(6)
            occupancy = idle(f"soak-{report['completed']}")
            sample = resources(workers, output)
            sample.update(
                elapsed=time.monotonic() - start,
                requests=report["completed"],
                idle=occupancy,
            )
            report["samples"].append(sample)
            report["elapsed"] = time.monotonic() - start
            target.write_text(json.dumps(report, indent=2) + "\n")
            print(
                f"M05 soak {report['completed']} requests, {report['elapsed']:.1f}s",
                flush=True,
            )
    baseline = report["samples"][2]
    last = report["samples"][-1]
    if baseline["processes"].keys() != last["processes"].keys():
        raise RuntimeError("Process set changed during soak")
    growth = {}
    for pid, before in baseline["processes"].items():
        after = last["processes"][pid]
        delta = {key: after[key] - before[key] for key in ("rss", "fds", "threads")}
        growth[pid] = delta
        if (
            delta["rss"] > report["limits"]["rss_growth_bytes"]
            or delta["fds"] > 8
            or delta["threads"] > 4
        ):
            raise RuntimeError(f"Post-warmup process resource growth: {pid}: {delta}")
    if baseline["native"].keys() != last["native"].keys():
        raise RuntimeError("Native observer process set changed")
    for key, before in baseline["native"].items():
        after = last["native"][key]
        if before["active"] != after["active"]:
            raise RuntimeError(f"Native resource counts grew: {key}")
    report.update(
        status="passed",
        resource_growth=growth,
        qualification="bounded duration and thresholds, not an unbounded leak-freedom proof",
    )
    target.write_text(json.dumps(report, indent=2) + "\n")
    return {key: value for key, value in report.items() if key != "samples"}


def run_soak(args, workers, http_port, complete, reference, idle, fault):
    try:
        return _run_soak(args, workers, http_port, complete, reference, idle, fault)
    except Exception as exc:
        target = args.output / "soak.json"
        report = json.loads(target.read_text()) if target.exists() else {}
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        target.write_text(json.dumps(report, indent=2) + "\n")
        raise
