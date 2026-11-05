# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute inside the selected runtime; inspect installed distributions without GPU allocation."""

import importlib.metadata
import json
import sys


def inspect_versions(expected):
    installed = {}
    for name in expected:
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed[name] = None
    return {
        "expected": expected,
        "installed": installed,
        "matches": all(installed[k] == v for k, v in expected.items()),
    }


if __name__ == "__main__":
    result = inspect_versions(json.loads(sys.argv[1]))
    print(json.dumps(result))
    raise SystemExit(0 if result["matches"] else 2)
