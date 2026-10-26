#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ensure dev tools and editable installs preserve the inherited runtime stack."""

import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("audit", "/opt/dynamo/bin/audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
base = json.loads(Path("/opt/dynamo/dev/runtime.json").read_text())
current = audit.snapshot()
changed = {
    k: (v, current["packages"].get(k))
    for k, v in base["packages"].items()
    if current["packages"].get(k) != v
}
introduced = sorted(set(current["pip_check"]) - set(base["pip_check"]))
if current["pip_check_exit"] not in (0, 1) or changed or introduced:
    raise RuntimeError(
        f"Runtime changed: {changed}; new dependency errors: {introduced}"
    )
Path("/opt/dynamo/dev/installed.json").write_text(json.dumps(current, indent=2) + "\n")
print("Runtime package versions preserved; no new dependency conflicts")
