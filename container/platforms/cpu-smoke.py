#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check CPU platform selection without importing accelerator frameworks."""

import importlib.abc
import json
import sys


class RejectDeviceSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {
            "torch",
            "torch_npu",
            "cuda",
            "pynvml",
            "maca",
            "acl",
        }:
            raise RuntimeError(f"CPU selection imported accelerator SDK: {fullname}")
        return None


sys.meta_path.insert(0, RejectDeviceSDK())
from dynamo.common.platform import PlatformRegistry  # noqa: E402

descriptor = PlatformRegistry().prepare("cpu", required=("runtime.control",))
print(json.dumps({"descriptor": descriptor, "no_device_sdk_imports": True}))
