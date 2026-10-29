#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run byte parity in one Ready Prefill/Decode pair on different nodes."""
import argparse
import concurrent.futures
import json
import shlex
import subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
p.add_argument("--namespace", default="dynamo-m08-1029")
p.add_argument("--name", default="m08-pd-rdma")
p.add_argument("--ssh-host", help="Optional configured cluster management SSH alias")
p.add_argument("--ssh-config", type=Path)
p.add_argument("--output", type=Path, required=True)
p.add_argument(
    "--churn",
    action="store_true",
    help="Stress repeated registrations; distinct from persistent KV buffers",
)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
kube = ["kubectl", "-n", a.namespace]


def command(words):
    if not a.ssh_host:
        return words
    ssh = ["ssh"]
    if a.ssh_config:
        ssh += ["-F", str(a.ssh_config)]
    return ssh + [a.ssh_host, shlex.join(words)]


pods = json.loads(
    subprocess.check_output(command(kube + ["get", "pods", "-o", "json"]), timeout=60)
)["items"]
selected = []
for role in ["prefill", "decode"]:
    selected.append(
        next(
            p
            for p in pods
            if p["metadata"]["name"].startswith(a.name + "-" + role + "-")
            and not p["metadata"].get("deletionTimestamp")
            and any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in p.get("status", {}).get("conditions", [])
            )
        )
    )
assert selected[0]["spec"]["nodeName"] != selected[1]["spec"]["nodeName"]
(a.output / "byte-pods.json").write_text(json.dumps(selected, indent=2))
source = (
    Path(__file__)
    .with_name("transfer-churn.py" if a.churn else "transfer.py")
    .read_bytes()
)
for pod in selected:
    subprocess.run(
        command(
            kube
            + [
                "exec",
                "-i",
                pod["metadata"]["name"],
                "-c",
                "main",
                "--",
                "sh",
                "-c",
                "cat > /tmp/m08-rdma-transfer.py",
            ]
        ),
        input=source,
        check=True,
        timeout=60,
    )


def run(rank):
    pod = selected[rank]
    words = kube + [
        "exec",
        pod["metadata"]["name"],
        "-c",
        "main",
        "--",
        "timeout",
        "240",
        "/opt/dynamo/bin/entrypoint.sh",
        "python",
        "/tmp/m08-rdma-transfer.py",
        "--platform",
        a.platform,
        "--rank",
        str(rank),
        "--host",
        pod["status"]["podIP"],
        "--peer",
        selected[0]["status"]["podIP"],
    ]
    r = subprocess.run(command(words), capture_output=True, timeout=270)
    log = (r.stdout + r.stderr).decode(errors="replace")
    (a.output / f"byte-rank-{rank}.log").write_text(log)
    records = [
        json.loads(line.partition("=")[2])
        for line in log.splitlines()
        if line.startswith("RDMA_PROBE_RESULT=")
    ]
    assert r.returncode == 0 and len(records) == 1 and records[0]["passed"], (
        rank,
        r.returncode,
    )
    assert len(records[0]["cases"]) == 20
    return records[0]


report = {"passed": False, "platform": a.platform, "ranks": []}
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        report["ranks"] = list(pool.map(run, [0, 1]))
    report["passed"] = True
finally:
    (a.output / "byte-summary.json").write_text(json.dumps(report, indent=2))
print("Device READ/WRITE byte parity passed for both ranks")
