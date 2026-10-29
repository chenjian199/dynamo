#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Manual three-stage Qwen2.5-Omni check on three dedicated local devices.

This requires the 7B Omni model because smaller VL fixtures have no talker or
code2wav stages. It is a manual large-device check, not a pre-merge CI test.
"""

import argparse
import asyncio
import base64
import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from uuid import uuid4

import yaml

from omni_soak import run as run_soak


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", required=True, choices=["cuda", "metax", "ascend"]
    )
    parser.add_argument("--devices", required=True, help="three physical ordinals")
    parser.add_argument("--mode", choices=["stages", "aggregated"], default="stages")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="/NFS_62/Models/Qwen_Series/Qwen2.5-Omni-7B")
    parser.add_argument("--soak-seconds", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    if args.soak_seconds < 0 or args.concurrency < 1:
        parser.error("soak-seconds must be nonnegative and concurrency positive")
    if args.soak_seconds and args.mode != "aggregated":
        parser.error("soak is only certified for aggregated mode")
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from dynamo.common.platform.versions import validate_vllm_versions
    from tests.utils.managed_process import ManagedProcess
    from tests.utils.port_utils import allocate_ports

    validate_vllm_versions(args.platform)
    devices = args.devices.split(",")
    if len(devices) != 3 or len(set(devices)) != 3:
        parser.error("three distinct devices are required")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "passed": False,
        "platform": args.platform,
        "mode": args.mode,
        "devices": devices,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("vllm", "torch", "vllm-omni")
        },
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model": args.model,
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (root / "adapter/m07/sources.txt").read_text().splitlines()
        },
    }
    ports = allocate_ports(11, start_port=10000 + secrets.randbelow(12000))
    namespace = "m07-omni-" + uuid4().hex[:10]
    env = os.environ.copy()
    env.update(
        DYN_NAMESPACE=namespace,
        DYN_DISCOVERY_BACKEND="file",
        DYN_REQUEST_PLANE="tcp",
        DYN_EVENT_PLANE="zmq",
        DYN_FILE_KV=str(args.output / "discovery"),
        HF_HUB_OFFLINE="1",
        DYN_PLATFORM=args.platform,
        TOKENIZERS_PARALLELISM="false",
        DYN_HTTP_PORT=str(ports[0]),
        DYN_DISABLE_NVDEC="1",
        DYN_LOG="info,handlers=debug",
    )
    config = {
        "async_chunk": False,
        "stages": [
            {
                "stage_id": stage,
                "devices": str(stage) if args.mode == "aggregated" else "0",
                "enforce_eager": True,
                "max_num_seqs": args.concurrency if args.soak_seconds else 1,
                "max_model_len": 2048,
                "max_num_batched_tokens": 2048,
                "gpu_memory_utilization": 0.6 if stage == 0 else 0.3,
                "enable_prefix_caching": False,
                "enable_flashinfer_autotune": False,
                "default_sampling_params": {
                    "temperature": 0,
                    "seed": 42,
                    "repetition_penalty": 1.05 if stage == 1 else 1.0,
                    "max_tokens": 32 if stage == 0 else 128,
                },
            }
            for stage in range(3)
        ],
    }
    for stage in config["stages"]:
        stage["engine_args"] = {
            key: stage.pop(key)
            for key in tuple(stage)
            if key not in ("stage_id", "devices", "default_sampling_params")
        }
    config_path = args.output / "stages.yaml"
    config_path.write_text(yaml.safe_dump(config))
    common = [
        sys.executable,
        "-m",
        "dynamo.vllm.omni",
        "--model",
        args.model,
        "--served-model-name",
        "m07-omni",
        "--stage-configs-path",
        str(config_path),
        "--enforce-eager",
        "--output-modalities",
        "audio",
        "--media-output-fs-url",
        (args.output / "media").as_uri(),
    ]
    processes = []
    try:
        with contextlib.ExitStack() as stack:
            for index in (range(5) if args.mode == "stages" else (0, 4)):
                child_env = env.copy()
                child_env["DYN_SYSTEM_PORT"] = str(ports[index + 1])
                child_env["DYN_FORWARDPASS_METRIC_PORT"] = str(ports[index + 6])
                if args.mode == "aggregated" and index == 0:
                    child_env.update(
                        CUDA_VISIBLE_DEVICES=args.devices,
                        MACA_VISIBLE_DEVICES=args.devices,
                        ASCEND_RT_VISIBLE_DEVICES=args.devices,
                    )
                    command = common
                elif index < 3:
                    device = devices[index]
                    child_env.update(
                        CUDA_VISIBLE_DEVICES=device,
                        MACA_VISIBLE_DEVICES=device,
                        ASCEND_RT_VISIBLE_DEVICES=device,
                    )
                    command = [*common, "--stage-id", str(index)]
                elif index == 3:
                    child_env["DYN_PLATFORM"] = "cpu"
                    command = [*common, "--omni-router"]
                else:
                    command = [sys.executable, "-m", "dynamo.frontend"]
                    child_env["DYN_PLATFORM"] = "cpu"
                process = stack.enter_context(
                    ManagedProcess(
                        command=command,
                        env=child_env,
                        working_dir=str(root),
                        log_dir=str(args.output / f"process-{index}"),
                        display_name=f"omni-{index}",
                        terminate_all_matching_process_names=False,
                    )
                )
                processes.append(process)
            deadline = time.monotonic() + 600
            endpoint = f"http://127.0.0.1:{ports[0]}"
            while True:
                exited = [
                    i for i, p in enumerate(processes) if p.proc.poll() is not None
                ]
                if exited:
                    raise RuntimeError(f"Omni processes exited: {exited}")
                try:
                    with urllib.request.urlopen(
                        endpoint + "/v1/models/m07-omni/ready", timeout=3
                    ) as response:
                        if json.load(response).get("ready"):
                            break
                except (urllib.error.URLError, TimeoutError):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("Omni readiness exceeded 600 seconds")
                time.sleep(2)
            payload = {
                "model": "m07-omni",
                "input": "Say hello in one short sentence.",
                "voice": "Chelsie",
                "response_format": "wav",
                "data_source": "b64_json",
                "max_new_tokens": 128,
            }
            req = urllib.request.Request(
                endpoint + "/v1/audio/speech",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as response:
                raw = response.read()
                content_type = response.headers.get_content_type()
            if content_type == "application/json":
                result = json.loads(raw)
                (args.output / "response.json").write_text(json.dumps(result, indent=2))
                if result.get("status") != "completed" or not result.get("data"):
                    raise ValueError(f"Omni returned no completed audio: {result}")
                audio = base64.b64decode(result["data"][0]["b64_json"], validate=True)
            else:
                audio = raw
            (args.output / "response.wav").write_bytes(audio)
            with wave.open(io.BytesIO(audio)) as wav:
                pcm = wav.readframes(wav.getnframes())
                frames = len(pcm) // (wav.getsampwidth() * wav.getnchannels())
                if frames == 0 or not any(pcm):
                    raise ValueError("Omni returned an empty/silent waveform")
                report["audio"] = {
                    "frames": frames,
                    "sample_rate": wav.getframerate(),
                    "sha256": hashlib.sha256(audio).hexdigest(),
                }
            if args.soak_seconds:
                report["soak"] = asyncio.run(
                    run_soak(
                        endpoint,
                        payload,
                        args.output,
                        processes,
                        args.soak_seconds,
                        args.concurrency,
                    )
                )
            report["passed"] = True
    except Exception as exc:
        report["error"] = repr(exc)
        raise
    finally:
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
