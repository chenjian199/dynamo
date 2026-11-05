#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Install explicit deployment-supplied capacity paths, never fake perf curves."""
import sys
from pathlib import Path

from aiconfigurator.sdk import perf_database

systems = {
    "cuda": ["h800_sxm", "h800_pcie"],
    "ascend": ["ascend_910b3"],
    "metax": ["metax_c500"],
}
root = Path(perf_database.get_systems_paths()[0])
for name in systems[sys.argv[1]]:
    path = root / (name + ".yaml")
    if path.exists() or path.is_symlink():
        # The runtime base may ship a capacity definition. Preserve it for audit;
        # serving this release still requires the deployment's explicit capacity.
        path.rename(path.with_suffix(".yaml.upstream"))
    path.symlink_to(Path("/opt/dynamo/platform-capacity") / (name + ".yaml"))
