#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Summarize the bounded Ascend DeepSeek campaign without hiding native failures."""

import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--results", type=Path, required=True)
p.add_argument("--raw", type=Path, required=True)
args = p.parse_args()


def result(name):
    return json.loads((args.results / (name + ".json")).read_text())


base = result("ascend-deepseek-functions-base")
mtp = result("ascend-deepseek-functions-mtp")
native = result("ascend-deepseek-native-base")
chat = result("ascend-deepseek-chat")
load = result("ascend-deepseek-load-30m")
snapshots = [
    json.loads(p.read_text()) for p in sorted(args.raw.glob("load-snapshot-*.json"))
]
valid = [r["resources"] for r in snapshots if r["resources"].get("processes")]
first, last = valid[0], valid[-1]
identities = {pid: r["created"] for pid, r in first["processes"].items()}
stable = all(
    {pid: r["created"] for pid, r in s["processes"].items()} == identities
    for s in valid
)
totals = [
    {k: sum(r[k] for r in s["processes"].values()) for k in ("rss", "fds", "threads")}
    for s in valid
]
metrics = (args.raw / "mtp-metrics.txt").read_text().splitlines()
spec = {
    k: sum(
        float(line.rsplit(" ", 1)[1])
        for line in metrics
        if line.startswith("vllm:" + k + "{")
    )
    for k in (
        "spec_decode_num_draft_tokens_total",
        "spec_decode_num_accepted_tokens_total",
    )
}
idle = json.loads((args.raw / "base-idle-resources.json").read_text())
report = {
    "qualification": "Candidate Python overlay on frozen M02 image; not M10 final-image certification",
    "functional": {
        "base": base["passed"],
        "mtp": mtp["passed"],
        "natural_language_smoke": chat["passed"],
        "natural_language_requests": sum(len(rows) for rows in chat["models"].values()),
        "mtp_counters": spec,
        "ladder_base_mtp_text_equal": all(
            a["responses"][0]["response"]["choices"][0]["text"]
            == b["responses"][0]["response"]["choices"][0]["text"]
            for a, b in zip(base["cases"], mtp["cases"], strict=True)
        ),
    },
    "determinism": {
        "status": "failed_native_reproduced_deferred",
        "strict_api_passed": result("ascend-deepseek-api-base")["passed"],
        "native_unique_token_sequences": native["unique_outputs"],
        "base_sse_parity": base["sse"]["parity"],
        "mtp_sse_parity": mtp["sse"]["parity"],
        "native_node_driver": "7.7 / 24.1.rc3",
        "base_node_driver": "7.5 / 25.2.0",
        "qualification": "Native reproduction does not prove identical numerical causes or implicate the driver",
    },
    "load": {
        "passed": load["passed"]
        and load["completed"] >= 512
        and load["elapsed"] >= 1800,
        "completed": load["completed"],
        "seconds": load["elapsed"],
        "concurrency": load["concurrency"],
        "deterministic": load["deterministic"],
    },
    "process_observation": {
        "qualification": "Partial-window process observations; initial sampling gap, no native-handle leak certification",
        "valid_samples": len(valid),
        "failed_samples": len(snapshots) - len(valid),
        "observed_seconds": last["time"] - first["time"],
        "stable_process_identity": stable,
        "first": totals[0],
        "last": totals[-1],
        "delta": {k: totals[-1][k] - totals[0][k] for k in totals[0]},
        "peak": {k: max(row[k] for row in totals) for k in totals[0]},
        "final_idle_occupancy": idle.get("occupancy"),
        "final_idle_zero": len(idle.get("occupancy", [])) == 3
        and all(float(line.rsplit(" ", 1)[1]) == 0 for line in idle["occupancy"]),
    },
    "deferred_or_not_covered": [
        "Exact output determinism (native reproduced)",
        "DeepSeek-specific hybrid-attention PD/KVBM connectors",
        "Cross-node TP16 and million-token context",
        "M08/M09 and final rebuilt-image M10 certification",
    ],
}
path = args.results / "summary.json"
path.write_text(json.dumps(report, indent=2) + "\n")
path.with_name(path.name + ".license").write_text(
    "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\nSPDX-License-Identifier: Apache-2.0\n"
)
print(json.dumps(report, indent=2))
