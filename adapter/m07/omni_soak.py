# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded HTTP audio concurrency/cancellation regression for omni-check.py."""

import asyncio
import base64
import hashlib
import io
import json
import time
import wave

import aiohttp
import psutil


def audio_summary(raw, content_type):
    if content_type == "application/json":
        result = json.loads(raw)
        if result.get("status") != "completed" or not result.get("data"):
            raise ValueError(f"No completed audio: {result}")
        raw = base64.b64decode(result["data"][0]["b64_json"], validate=True)
    with wave.open(io.BytesIO(raw)) as wav:
        pcm = wav.readframes(wav.getnframes())
        frames = len(pcm) // (wav.getsampwidth() * wav.getnchannels())
        if not frames or not any(pcm) or wav.getframerate() != 24000:
            raise ValueError("Expected nonempty, nonsilent 24 kHz WAV")
        return {
            "frames": frames,
            "sample_rate": wav.getframerate(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }


def resources(processes, output):
    tree = {}
    for managed in processes:
        if managed.proc.poll() is not None:
            raise RuntimeError(f"Service exited: {managed.proc.pid}")
        parent = psutil.Process(managed.proc.pid)
        for process in [parent, *parent.children(recursive=True)]:
            try:
                tree[process.pid] = {
                    "rss": process.memory_info().rss,
                    "fds": process.num_fds(),
                    "name": process.name(),
                }
            except psutil.NoSuchProcess:
                continue
    files = list((output / "media").rglob("*"))
    return {
        "processes": tree,
        "rss": sum(p["rss"] for p in tree.values()),
        "fds": sum(p["fds"] for p in tree.values()),
        "media_files": sum(p.is_file() for p in files),
    }


async def run(endpoint, payload, output, processes, duration, concurrency):
    started = time.monotonic()
    records, samples = [], []
    summary = {
        "passed": False,
        "duration_requested_seconds": duration,
        "concurrency": concurrency,
        "cancel_delay_seconds": 1.0,
        "completed": 0,
        "client_cancelled": 0,
        "rounds": 0,
        "rss_growth_limit_bytes": 2 * 1024**3,
        "fd_growth_limit": 32,
    }
    progress = output / "soak-progress.json"
    log_path = processes[0].log_path

    def save():
        summary["elapsed_seconds"] = time.monotonic() - started
        progress.write_text(json.dumps(summary, indent=2) + "\n")

    async def request(session, label):
        begin = time.monotonic()
        body = dict(payload, input=f"Say hello and welcome. Request {label}.")
        async with session.post(endpoint + "/v1/audio/speech", json=body) as response:
            raw = await response.read()
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}: {raw[:500]!r}")
            result = audio_summary(raw, response.content_type)
        record = {"label": label, "elapsed_seconds": time.monotonic() - begin, **result}
        records.append(record)
        summary["completed"] += 1
        return record

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=180)
        ) as session:
            # Establish allocator/cache baseline after repeated full pipeline execution.
            for i in range(3):
                await asyncio.gather(
                    *(request(session, f"warm-{i}-{j}") for j in range(concurrency))
                )
            baseline = resources(processes, output)
            baseline["elapsed_seconds"] = time.monotonic() - started
            samples.append(baseline)
            workload_start = time.monotonic()
            while time.monotonic() - workload_start < duration:
                round_id = summary["rounds"]
                if round_id % 5 == 0:
                    task = asyncio.create_task(request(session, f"cancel-{round_id}"))
                    await asyncio.sleep(1.0)
                    if task.done():
                        await task
                        raise RuntimeError(
                            "Cancellation arrived after completion; test is inconclusive"
                        )
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        summary["client_cancelled"] += 1
                # Every cancellation is followed by real concurrent successful requests.
                await asyncio.gather(
                    *(
                        request(session, f"round-{round_id}-{j}")
                        for j in range(concurrency)
                    )
                )
                summary["rounds"] += 1
                sample = resources(processes, output)
                sample["elapsed_seconds"] = time.monotonic() - started
                samples.append(sample)
                save()
            await request(session, "final-recovery")
            await asyncio.sleep(3)
            final = resources(processes, output)
            final["elapsed_seconds"] = time.monotonic() - started
            samples.append(final)
            summary["workload_seconds"] = time.monotonic() - workload_start
            summary["rss_growth_bytes"] = final["rss"] - baseline["rss"]
            summary["fd_growth"] = final["fds"] - baseline["fds"]
            summary["media_file_growth"] = (
                final["media_files"] - baseline["media_files"]
            )
            with open(log_path) as stream:
                log = stream.read()
            summary["backend_abort_log_count"] = log.count("Aborted Request ID:")
            summary["backend_abort_errors"] = log.count("Error in abort monitor")
            summary["abort_shield_background_count"] = log.count(
                "Abort shielded from cancellation"
            )
            # Log confirms the backend received cancellation; it is not proof that
            # every engine allocation was reclaimed. Resource bounds cover this run only.
            checks = {
                "cancellations_observed": summary["client_cancelled"] > 0
                and summary["backend_abort_log_count"] >= summary["client_cancelled"],
                "no_abort_errors": summary["backend_abort_errors"] == 0,
                "rss_bounded": summary["rss_growth_bytes"]
                <= summary["rss_growth_limit_bytes"],
                "fds_bounded": summary["fd_growth"] <= summary["fd_growth_limit"],
                "no_media_growth": summary["media_file_growth"] == 0,
                "process_set_stable": set(final["processes"])
                == set(baseline["processes"]),
                "duration_met": summary["workload_seconds"] >= duration,
            }
            summary["checks"] = checks
            summary["passed"] = all(checks.values())
            if not summary["passed"]:
                raise AssertionError(f"Omni soak failed: {checks}")
    except Exception as exc:
        summary["error"] = repr(exc)
        raise
    finally:
        save()
        (output / "soak-result.json").write_text(json.dumps(summary, indent=2) + "\n")
        (output / "soak-requests.json").write_text(json.dumps(records, indent=2) + "\n")
        (output / "soak-resources.json").write_text(
            json.dumps(samples, indent=2) + "\n"
        )
    return summary
