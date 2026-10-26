#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run one M07 suite inside an existing developer container and record its source."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", required=True, choices=["cuda", "metax", "ascend"]
    )
    parser.add_argument(
        "--device", type=int, default=0, help="physical device to expose"
    )
    parser.add_argument(
        "--suite",
        required=True,
        choices=["gms", "legacy", "media", "unit", "encoder", "omni-unit"],
    )
    parser.add_argument(
        "--encoder-device", type=int, help="separate physical encoder device"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="/NFS_62/Models/Qwen_Series/Qwen3-0.6B")
    args = parser.parse_args()
    from dynamo.common.platform.versions import validate_vllm_versions

    validate_vllm_versions(args.platform)
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from tests.utils.managed_process import ManagedProcess

    args.output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(
        DYN_PLATFORM=args.platform,
        PLATFORM=args.platform,
        DEVICES=str(args.device),
        M07_DEVICE="0",
        TP="1",
        DP="1",
        HF_HUB_OFFLINE="1",
        M07_MODEL=args.model,
    )
    if args.encoder_device is not None:
        env["ENCODER_DEVICES"] = str(args.encoder_device)
    if args.platform == "ascend":
        env["ASCEND_RT_VISIBLE_DEVICES"] = str(args.device)
    else:
        env["CUDA_VISIBLE_DEVICES"] = str(args.device)
    if args.platform == "metax":
        env.update(
            MACA_VISIBLE_DEVICES=str(args.device),
            UCX_PROTO_ENABLE="y",
            UCX_TLS="maca_ipc,maca_copy,tcp",
        )
    command = [sys.executable, "-m", "pytest", "-v"]
    if args.suite == "gms":
        command += ["lib/gpu_memory_service/tests/test_platform_vmm.py"]
    elif args.suite == "legacy":
        command += ["lib/gpu_memory_service/tests/test_torch_integration.py"]
    elif args.suite == "media":
        command += ["adapt/m07/test_multimodal.py"]
        if args.platform == "ascend":
            # NIXL Python is not installed in the pinned Ascend image. LOCAL is
            # the certified same-host transport; do not install CUDA NIXL wheels.
            command += ["-k", "not nixl_read and not nixl_write and not write_late"]
    elif args.suite == "omni-unit":
        overlay = Path("/opt/dynamo/omni-overlay")
        if args.platform != "cuda" and overlay.is_dir():
            env["PYTHONPATH"] = str(overlay) + os.pathsep + env.get("PYTHONPATH", "")
        command += [
            "--noconftest",
            "-W",
            "ignore:`torch.cuda.amp.custom_fwd:FutureWarning",
            "-W",
            "ignore:`torch.cuda.amp.custom_bwd:FutureWarning",
            "-W",
            "ignore:fla package is not installed:UserWarning",
            "components/src/dynamo/vllm/tests/omni/test_omni_stage_worker.py",
        ]
        if args.platform != "ascend":
            command += [
                "components/src/dynamo/vllm/tests/omni/test_omni_nixl_connector.py"
            ]
    elif args.suite == "unit":
        command += ["--noconftest"]  # These isolated units need no NATS/etcd services.
        command += [
            "components/src/dynamo/common/tests/multimodal/test_media_lifecycle.py",
            "components/src/dynamo/common/tests/multimodal/test_tensor_contract.py",
            "components/src/dynamo/common/tests/multimodal/test_nvdec_decoder.py",
            "components/src/dynamo/common/tests/memory/test_multimodal_embedding_cache_manager.py",
            "components/src/dynamo/common/platform/tests",
            "components/src/dynamo/vllm/tests/multimodal_utils/test_vllm_embedding_loader.py",
            "lib/bindings/python/tests/test_nixl_connect_unit.py",
        ]
        if args.platform != "ascend":
            command += [
                "lib/gpu_memory_service/tests/test_vmm_provider_contract.py",
                "lib/gpu_memory_service/tests/test_v1_memory_manager.py",
                "lib/gpu_memory_service/tests/test_cli_server.py",
                "lib/gpu_memory_service/tests/test_v1_checkpoint.py",
            ]
    else:
        env["MODEL_PATH"] = "/NFS_62/Models/Qwen_Series/Qwen3-VL-2B-Instruct"
        transfer_dir = args.output.resolve() / "tmp"
        transfer_dir.mkdir()
        env["TMPDIR"] = str(transfer_dir)
        command = [
            sys.executable,
            "adapt/m03/run-case.py",
            "--output",
            str(args.output / "case"),
            "--launch-script",
            "adapt/m07/serve-mm.sh",
            "--startup-timeout",
            "240",
            "--check",
            "vision",
            "--",
            "--enable-multimodal",
            "--limit-mm-per-prompt",
            '{"image":1,"video":0}',
            "--mm-processor-kwargs",
            '{"max_pixels":200704}',
        ]
    report = {
        "started": datetime.now(timezone.utc).isoformat(),
        "platform": args.platform,
        "device": args.device,
        "encoder_device": args.encoder_device,
        "suite": args.suite,
        "command": command,
        "passed": False,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (root / "adapt/m07/sources.txt").read_text().splitlines()
        },
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "vllm", "ai-dynamo")
        },
    }
    try:
        with ManagedProcess(
            command=command,
            env=env,
            working_dir=str(root),
            log_dir=str(args.output.resolve()),
            display_name="m07",
            terminate_all_matching_process_names=False,
        ) as process:
            returncode = process.proc.wait(timeout=900)
        report.update(returncode=returncode, passed=returncode == 0)
        if args.suite == "encoder":
            remaining = list(transfer_dir.glob("encoder_cache.*.safetensors"))
            report["remaining_embedding_files"] = [path.name for path in remaining]
            report["passed"] = report["passed"] and not remaining
    except subprocess.TimeoutExpired:
        report["error"] = "suite exceeded 900 second deadline"
    finally:
        report["finished"] = datetime.now(timezone.utc).isoformat()
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("platform", "suite", "passed")}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
