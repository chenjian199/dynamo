#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate a 10-minute VP9 fixture or check repeated bounded video decoding."""

import argparse
import asyncio
import base64
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path


def generate(path):
    import av
    import numpy as np

    with av.open(str(path), mode="w") as output:
        stream = output.add_stream("libvpx-vp9", rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
        stream.options = {"deadline": "realtime", "cpu-used": "8", "threads": "2"}
        pixels = np.zeros((180, 320, 3), dtype=np.uint8)
        pixels[..., 0] = 255
        for _ in range(6000):
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


async def check(path, report):
    import psutil

    from dynamo.common.multimodal.video_loader import VideoLoader, _decode_slots

    uri = "data:video/webm;base64," + base64.b64encode(path.read_bytes()).decode()
    loader = VideoLoader(num_frames=32)
    process = psutil.Process()
    samples = []
    for iteration in range(22):
        frames, metadata = await loader.load_video(uri)
        assert frames.shape == (32, 180, 320, 3), frames.shape
        assert frames[..., 0].mean() > 200 and frames[..., 1:].mean() < 25
        del frames
        gc.collect()
        samples.append({"rss": process.memory_info().rss, "fds": process.num_fds()})
    # Cancel requests while CPU work may still be in progress. The final
    # successful decode demonstrates that permits return after thread completion.
    for _ in range(10):
        tasks = [asyncio.create_task(loader.load_video(uri)) for _ in range(4)]
        await asyncio.sleep(0.01)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    frames, _ = await asyncio.wait_for(loader.load_video(uri), 60)
    assert frames.shape[0] == 32
    slots = _decode_slots[asyncio.get_running_loop()]
    async with asyncio.timeout(60):
        while slots._value != 2:
            await asyncio.sleep(0.01)
    del frames
    gc.collect()
    final = {"rss": process.memory_info().rss, "fds": process.num_fds()}
    baseline = samples[2]
    assert final["fds"] <= baseline["fds"] + 2, (baseline, final)
    assert final["rss"] <= baseline["rss"] + 128 * 1024**2, (baseline, final)
    report.update(
        passed=True,
        iterations=22,
        cancelled_requests=40,
        samples=samples,
        final=final,
        metadata=metadata,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.generate:
        generate(args.video)
        return
    if args.output is None:
        parser.error("--output is required for validation")
    report = {
        "passed": False,
        "platform": os.environ["DYN_PLATFORM"],
        "fixture_sha256": hashlib.sha256(args.video.read_bytes()).hexdigest(),
        "fixture_seconds": 600,
        "fixture_frames": 6000,
        "vllm": importlib.metadata.version("vllm"),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    try:
        asyncio.run(check(args.video, report))
    finally:
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
