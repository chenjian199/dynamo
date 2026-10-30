# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reconstruct the CUDA Qwen32 readiness symptom from retained rounded FPM logs."""
import argparse
import asyncio
import dataclasses
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import nnls

from dynamo.common.forward_pass_metrics import (
    ForwardPassMetrics,
    ScheduledRequestMetrics,
)
from dynamo.planner.config.defaults import SubComponentType
from dynamo.planner.config.planner_config import PlannerConfig
from dynamo.planner.core.perf_model.aic_adapter import PlannerEnginePerfModel
from dynamo.planner.core.types import EngineCapabilities
from dynamo.planner.monitoring.perf_metrics import fetch_pre_deployment_metrics

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--case", type=Path, required=True)
parser.add_argument("--planner", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
a = parser.parse_args()
p = a.case
objs = list(yaml.safe_load_all((p / "online-v9.yaml").read_text()))
c = PlannerConfig.model_validate(
    next(
        json.loads(o["data"]["planner_config.json"])
        for o in objs
        if o.get("kind") == "ConfigMap" and "planner_config.json" in o.get("data", {})
    )
)
caps = EngineCapabilities(
    **json.loads((p / "offline-verification-final.json").read_text())["capabilities"]
)
m = PlannerEnginePerfModel(worker_type="decode", config=c, capabilities=caps)
samples = asyncio.run(
    fetch_pre_deployment_metrics(
        None,
        "unused",
        None,
        str(p / "profile-v1/verification-mounted"),
        SubComponentType.DECODE,
        profile_identity=c.profile_identity,
        source="files",
    )
)
m.load_benchmark_fpms(samples)


def query():
    return m.find_engine_capacity_rps(isl=512, osl=128, itl_sla_ms=c.itl_ms)


initial = query()
initial_diag = m._engine_diagnostics()
first_unavailable = None
observed = list(samples)
rows = []
for line in a.planner.read_text().splitlines():
    f = re.search(
        r"FPM decode engine (\d+):dp(\d+): wall_time=([\d.]+)s, sched\(prefill_tok=(\d+), prefill_req=(\d+), decode_kv=(\d+), decode_req=(\d+)\)",
        line,
    )
    if not f:
        continue
    wid, rank, wall, pt, pr, kv, n = f.groups()
    fpm = ForwardPassMetrics(
        worker_id=wid,
        dp_rank=int(rank),
        counter_id=len(rows) + 1,
        wall_time=float(wall),
        scheduled_requests=ScheduledRequestMetrics(
            sum_prefill_tokens=int(pt),
            num_prefill_requests=int(pr),
            sum_decode_kv_tokens=int(kv),
            num_decode_requests=int(n),
        ),
    )
    if fpm.wall_time > 0 and int(n) > 0 and int(pt) == 0:
        observed.append(fpm)
    m.add_observations({(wid, int(rank)): fpm})
    q = query()
    if q is None:
        diag = m._engine_diagnostics()
        if first_unavailable is None:
            first_unavailable = {
                "logged_at": re.search(r"\d{4}-\d\d-\d\dT[\d:.]+Z", line)[0],
                "diagnostics": diag,
                "observation_index": len(rows),
            }
            # Before bucket eviction, the logged decode feature vectors can be
            # fitted independently with an unconstrained intercept and NNLS
            # slopes, matching the upstream regression's constraint.
            if len(observed) <= c.max_num_fpm_samples:
                x = np.array(
                    [
                        [
                            f.scheduled_requests.num_decode_requests,
                            f.scheduled_requests.sum_decode_kv_tokens,
                        ]
                        for f in observed
                    ],
                    dtype=float,
                )
                y = np.array([f.wall_time * 1000 for f in observed])
                slopes, residual = nnls(x - x.mean(axis=0), y - y.mean())
                first_unavailable["independent_nnls"] = {
                    "observations": len(observed),
                    "feature_names": ["decode_requests", "decode_kv_tokens"],
                    "slopes": slopes.tolist(),
                    "intercept_ms": float(y.mean() - x.mean(axis=0) @ slopes),
                    "residual_norm": float(residual),
                    "has_positive_load_signal": bool((slopes > 0).any()),
                }
    rows.append(dataclasses.asdict(q) if q else None)
result = {
    "scope": "diagnostic reconstruction from rounded FPM log fields, not exact counter/rank replay",
    "aisimulate_version": importlib.metadata.version("aisimulate"),
    "source_log_sha256": hashlib.sha256(a.planner.read_bytes()).hexdigest(),
    "initial_capacity": dataclasses.asdict(initial),
    "initial_diagnostics": initial_diag,
    "first_unavailable": first_unavailable,
    "observations": len(rows),
    "unavailable_queries": sum(x is None for x in rows),
    "final_diagnostics": m._engine_diagnostics(),
}
a.output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
