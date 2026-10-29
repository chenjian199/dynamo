#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run inside the frozen runtime image; emit evidence without installing packages."""

import importlib
import importlib.metadata
import json
import os
import sys
import traceback


def main():
    platform = os.environ["DYN_PLATFORM"]
    report = {"platform": platform, "checks": {}, "passed": False}

    def check(name, fn):
        try:
            report["checks"][name] = {"passed": True, "result": fn()}
        except Exception:
            report["checks"][name] = {"passed": False, "error": traceback.format_exc()}
        print(json.dumps({name: report["checks"][name]}, default=str), flush=True)

    def versions():
        from dynamo.common.platform.versions import validate_vllm_versions

        validate_vllm_versions(platform)
        names = ["ai-dynamo", "ai-dynamo-runtime", "kvbm", "vllm", "torch"]
        names += ["torch-npu", "vllm-ascend"] if platform == "ascend" else ["nixl-cu13"]
        return {name: importlib.metadata.version(name) for name in names}

    def identity():
        from dynamo.common.platform import PlatformRegistry

        return PlatformRegistry().prepare(platform)

    def memory():
        import torch

        if platform == "ascend":
            import torch_npu  # noqa: F401

        api = torch.npu if platform == "ascend" else torch.cuda
        kind = "npu" if platform == "ascend" else "cuda"
        count = api.device_count()
        assert count == 1, f"Expected one allocated device, got {count}"
        api.set_device(0)
        source = torch.arange(1024 * 1024, dtype=torch.float32)
        stream = api.Stream()
        event = api.Event()
        with api.stream(stream):
            device = source.to(f"{kind}:0")
            result = device * 2 + 3
            event.record()
        event.synchronize()
        actual = result.cpu()
        assert torch.equal(actual, source * 2 + 3)
        a = torch.ones((128, 128), device=f"{kind}:0")
        assert torch.allclose((a @ a).cpu(), torch.full((128, 128), 128.0))
        api.synchronize()
        return {"visible_devices": count, "copy_arithmetic_stream_event_matmul": True}

    check("versions", versions)
    check("identity", identity)
    check("memory", memory)
    check(
        "module_paths",
        lambda: {
            name: getattr(importlib.import_module(name), "__file__", None)
            for name in ["dynamo.common.platform", "dynamo._core", "kvbm", "vllm"]
        },
    )
    report["passed"] = all(c["passed"] for c in report["checks"].values())
    print("REGRESSION_RESULT=" + json.dumps(report, default=str), flush=True)
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
