#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise packaged native KVBM with two allocated cards; preserve plugin visibility."""

import itertools
import json
import os
import subprocess
import tempfile
from pathlib import Path

from dynamo.common.platform import PlatformRegistry

platform = os.environ["DYN_PLATFORM"]
descriptor = PlatformRegistry().prepare(platform)
assert len(descriptor["devices"]) == 2, descriptor
print(json.dumps({"descriptor": descriptor}), flush=True)
binary = "/opt/dynamo/native/native_replicated"
results = []
with tempfile.TemporaryDirectory(prefix="native-regression-") as base:
    for outer, width in itertools.product((1, 2), (2, 4, 8)):
        path = Path(base) / f"{outer}-{width}"
        path.mkdir()
        children, handles = [], []
        try:
            for rank in (0, 1):
                log = (path / f"{rank}.log").open("w")
                handles.append(log)
                children.append(
                    subprocess.Popen(
                        [
                            binary,
                            platform,
                            str(rank),
                            str(rank),
                            "2",
                            str(path),
                            str(outer),
                            str(width),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                )
            for child in children:
                assert child.wait(timeout=180) == 0, "native rank failed"
            for rank in (0, 1):
                text = (path / f"{rank}.log").read_text()
                print(text, flush=True)
                assert "passed=true" in text
            results.append({"outer": outer, "width": width, "passed": True})
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=10)
            for handle in handles:
                handle.close()
            for log in path.glob("*.log"):
                if len(results) == 0 or not all(c.returncode == 0 for c in children):
                    print(log.name + "\n" + log.read_text(), flush=True)
print(
    "REGRESSION_RESULT="
    + json.dumps({"passed": True, "platform": platform, "cases": results}),
    flush=True,
)
