#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Use the shared M03 driver against the installed runtime, without a Git checkout."""

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[5]
    driver = runpy.run_path(
        str(root / "adapter/modules/m03-engine-integration/common/engine/run-case.py")
    )
    driver["main"](["--source-mode", "installed", *sys.argv[1:]])
