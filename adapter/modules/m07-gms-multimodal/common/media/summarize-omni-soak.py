#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Summarize a finished Omni soak without changing the runner's verdict."""

import argparse
import hashlib
import json
import statistics
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run", required=True, type=Path)
args = parser.parse_args()
run = args.run
result = json.loads((run / "result.json").read_text())
soak = json.loads((run / "soak-result.json").read_text())
samples = json.loads((run / "soak-resources.json").read_text())
requests = json.loads((run / "soak-requests.json").read_text())
latencies = sorted(r["elapsed_seconds"] for r in requests)
window = max(1, len(samples) // 4)
summary = {
    "passed": result["passed"] and soak["passed"],
    "platform": result["platform"],
    "packages": result["packages"],
    "soak": soak,
    "rss_initial_bytes": samples[0]["rss"],
    "rss_final_bytes": samples[-1]["rss"],
    "rss_peak_bytes": max(s["rss"] for s in samples),
    "rss_first_quarter_median_bytes": statistics.median(
        s["rss"] for s in samples[:window]
    ),
    "rss_last_quarter_median_bytes": statistics.median(
        s["rss"] for s in samples[-window:]
    ),
    "fd_initial": samples[0]["fds"],
    "fd_final": samples[-1]["fds"],
    "fd_peak": max(s["fds"] for s in samples),
    "latency_seconds": {
        "p50": statistics.median(latencies),
        "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
        "max": max(latencies),
    },
    "distinct_audio_hashes": len({r["sha256"] for r in requests}),
    "audio_frames_range": [
        min(r["frames"] for r in requests),
        max(r["frames"] for r in requests),
    ],
    "limits": [
        f"{soak['duration_requested_seconds']}s bounded regression; not indefinite leak freedom",
        "HTTP disconnect + backend abort logs; no per-stage KV counters",
        "Nonempty nonsilent waveform; no semantic/audio quality certification",
        "Host RSS sums include shared pages; not unique physical RAM",
    ],
    "raw_sha256": {
        name: hashlib.sha256((run / name).read_bytes()).hexdigest()
        for name in (
            "result.json",
            "soak-result.json",
            "soak-resources.json",
            "soak-requests.json",
            "gpu-samples.jsonl",
        )
    },
}
(run / "soak-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
