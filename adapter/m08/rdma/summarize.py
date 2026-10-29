#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate bounded RDMA evidence; do not equate serving success with all stress tests."""
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


root = Path(__file__).resolve().parent / "results"
summary = {
    "scope": "Two-node persistent device-buffer RDMA and Operator 2P3D, Qwen3-0.6B",
    "platforms": {},
}
for platform, hosts, regression in [
    ("cuda", ["h800-6", "h800-14"], "regression-final"),
    ("ascend", ["huawei-52", "huawei-55"], "regression"),
]:
    p = root / platform
    evidence = p / "evidence-rdma"
    report = load(evidence / regression / "summary.json")
    assert report["passed"]
    pairs = load(evidence / "pairs/pairs.json")
    assert pairs["passed"] and len(pairs["pairs"]) == 6
    assert load(evidence / "cleanup.json")["passed"]
    assert not any(
        x["metadata"]["name"].startswith(("m08-pd-rdma-", "m08-rdma-byte-"))
        for x in load(p / "final-pods.json")["items"]
    )
    initial = load(evidence / regression / "initial.pods.json")["items"]
    nodes = {
        role: {
            x["spec"]["nodeName"]
            for x in initial
            if "-" + role + "-" in x["metadata"]["name"]
        }
        for role in ["prefill", "decode"]
    }
    assert len(nodes["prefill"]) == len(nodes["decode"]) == 1
    assert nodes["prefill"].isdisjoint(nodes["decode"])
    for role, count in [("prefill", 2), ("decode", 3)]:
        assert sum("-" + role + "-" in x["metadata"]["name"] for x in initial) == count
    cancellations = 0
    for label in ["initial-api", "recovery-api"]:
        api = load(evidence / regression / (label + ".json"))
        assert api["passed"] and api["sse_parity"] and api["post_cancel_recovery"]
        cancellations += api["cancellations"]
    if platform == "cuda":
        assert load(p / "byte-churn-passed/byte-summary.json")["passed"]
    else:
        churn = p / "byte-registration-churn-failed"
        assert not load(churn / "byte-summary.json")["passed"]
        assert "HcclRegisterGlobalMem" in (churn / "byte-rank-1.log").read_text()
    byte = load(p / "byte-final/byte-summary.json")
    assert byte["passed"] and len(byte["ranks"]) == 2
    for rank in byte["ranks"]:
        assert rank["passed"] and rank["device_allocated_after"] == 0
        assert len(rank["cases"]) == 20
        assert {x["operation"] for x in rank["cases"]} == {"READ", "WRITE"}
        assert {x["bytes"] for x in rank["cases"]} == {
            4,
            4096,
            524288,
            4194304,
            16777216,
        }
    logs = "\n".join(
        (p / "byte-final" / f"byte-rank-{i}.log").read_text() for i in [0, 1]
    )
    if platform == "cuda":
        assert "inter-node" in logs and "rc_mlx5/mlx5_10:1" in logs
        assert "into cuda/GPU0 from cuda/dev[0]" in logs
        assert "from cuda/GPU0 to cuda/dev[0]" in logs and "zero-copy" in logs
        for rank in byte["ranks"]:
            assert "tcp" not in rank["environment"]["UCX_TLS"].split(",")
            assert rank["environment"]["UCX_IB_GPU_DIRECT_RDMA"] == "yes"
    else:
        assert "AscendDirectTransport" in logs
        assert "mem type:device" in logs
        assert "Failed to deregister" not in logs
    deltas = {}
    for host in hosts:
        before, after = (
            load(p / (host + "-" + phase + ".json")) for phase in ["before", "final"]
        )
        changed = {}
        for dev, values in after["devices"].items():
            fields = (
                [
                    "counters/port_xmit_data",
                    "counters/port_rcv_data",
                    "hw_counters/rx_read_requests",
                    "hw_counters/rx_write_requests",
                    "hw_counters/rx_icrc_encapsulated",
                ]
                if platform == "cuda"
                else [
                    "roce_tx_rc_pkt_num",
                    "roce_rx_rc_pkt_num",
                    "roce_tx_err_pkt_num",
                    "roce_rx_err_pkt_num",
                ]
            )
            if platform == "cuda" and dev != "mlx5_10":
                continue
            changed[dev] = {k: values[k] - before["devices"][dev][k] for k in fields}
        if platform == "cuda":
            assert changed["mlx5_10"]["counters/port_xmit_data"] > 0
            assert changed["mlx5_10"]["counters/port_rcv_data"] > 0
            assert changed["mlx5_10"]["hw_counters/rx_icrc_encapsulated"] == 0
        else:
            assert sum(d["roce_tx_rc_pkt_num"] for d in changed.values()) > 0
            assert sum(d["roce_rx_rc_pkt_num"] for d in changed.values()) > 0
            assert all(
                d["roce_tx_err_pkt_num"] == d["roce_rx_err_pkt_num"] == 0
                for d in changed.values()
            )
        deltas[host] = changed
    summary["platforms"][platform] = {
        "rdma_serving_passed": True,
        "pd_pairs": 6,
        "cancellations": cancellations,
        "decode_recreation": "passed",
        "device_byte_cases_per_rank": 20,
        "cleanup": "passed",
        "counter_deltas": deltas,
        "registration_churn": (
            "passed"
            if platform == "cuda"
            else "failed_native_adxl_hccl_registration_lifecycle"
        ),
    }
summary["limitations"] = [
    "Ascend repeated register/deregister with changing addresses/sizes failed; persistent KV-style buffers passed.",
    "CUDA uses one selected RoCE rail; no all-rail bandwidth, NCCL collective or production SLA certification.",
    "Ascend DRA awaits separate >=1.34 cluster; MetaX has no cluster.",
]
(root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(
    json.dumps(
        {
            k: {n: v for n, v in d.items() if n != "counter_deltas"}
            for k, d in summary["platforms"].items()
        },
        indent=2,
    )
)
