#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Record independent CUDA media transport cases and actual decoder availability."""

import asyncio
import base64
import importlib.util
import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
import torch

spec = importlib.util.spec_from_file_location(
    "media_checks", "/media-tests/test_multimodal.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
report = {"passed": False, "cases": []}


async def case(name, call):
    try:
        await asyncio.wait_for(call(), 120)
        result = {"name": name, "passed": True}
    except Exception as error:
        result = {"name": name, "passed": False, "error": repr(error)}
    report["cases"].append(result)
    print(json.dumps(result), flush=True)


async def h264():
    import av

    from dynamo.common.multimodal.video_loader import VideoLoader

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "red.mp4"
        with av.open(str(path), mode="w") as output:
            stream = output.add_stream("libx264", rate=10)
            stream.width = 64
            stream.height = 64
            stream.pix_fmt = "yuv420p"
            pixels = np.zeros((64, 64, 3), dtype=np.uint8)
            pixels[..., 0] = 255
            for _ in range(20):
                for packet in stream.encode(
                    av.VideoFrame.from_ndarray(pixels, format="rgb24")
                ):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        uri = "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode()
        frames, metadata = await VideoLoader(num_frames=4).load_video(uri)
        assert frames.shape == (4, 64, 64, 3), frames.shape
        assert frames[..., 0].mean() > 200 and frames[..., 1:].mean() < 20
        report["h264_metadata"] = metadata


async def main():
    for transport in ("local", "nixl_read", "nixl_write"):
        for dtype in (torch.float32, torch.float16, torch.bfloat16):
            with tempfile.TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as patch:
                await case(
                    str(dtype) + "/" + transport,
                    lambda: module.test_device_embedding_roundtrip(
                        transport, dtype, Path(directory), patch
                    ),
                )
    await case(
        "late_write_quarantine",
        module.test_write_late_submission_after_receiver_timeout,
    )
    with tempfile.TemporaryDirectory() as directory:
        await case("vp9_decode", lambda: module.test_cpu_video_decode(Path(directory)))
    await case("h264_decode", h264)
    report["passed"] = all(c["passed"] for c in report["cases"])
    print("REGRESSION_RESULT=" + json.dumps(report), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
