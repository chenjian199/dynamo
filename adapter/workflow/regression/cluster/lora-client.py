#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reuse M03's nonzero-adapter lifecycle and cache isolation assertions."""

import importlib.util
import json
import os
import time
import urllib.error
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("advanced", "/fixtures/advanced-smoke.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.FRONTEND = "http://lora-frontend:8000"
module.WORKER = "http://lora-worker-api:8081"
deadline = time.monotonic() + 900
while True:
    try:
        ready = module.request(module.FRONTEND + "/v1/models/lora-model/ready")
        if ready.get("ready"):
            break
    except (urllib.error.URLError, TimeoutError):
        pass
    if time.monotonic() > deadline:
        raise TimeoutError("LoRA worker startup deadline")
    time.sleep(5)
report = {
    "passed": False,
    "fixture": "deterministic nonzero Qwen3 adapter; lifecycle only, not trained-model quality",
}
try:
    module.lora(SimpleNamespace(platform=os.environ["DYN_PLATFORM"]), report)
    report["passed"] = True
finally:
    print("REGRESSION_RESULT=" + json.dumps(report), flush=True)
