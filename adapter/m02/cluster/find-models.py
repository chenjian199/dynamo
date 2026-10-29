#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only host model discovery; report model metadata, never arbitrary configs."""

import json
import os
import sys
import time
from pathlib import Path

started = time.monotonic()
errors = []
results = []
directories = 0
seen = set()
skip = {"/proc", "/sys", "/dev", "/run"}
print(
    json.dumps({"scan_pid": os.getpid(), "started": time.time()}),
    file=sys.stderr,
    flush=True,
)
for directory, children, files in os.walk(
    "/", onerror=lambda e: errors.append(str(e.filename)), followlinks=False
):
    try:
        stat = os.stat(directory)
    except OSError:
        errors.append(directory)
        children[:] = []
        continue
    identity = (stat.st_dev, stat.st_ino)
    if identity in seen:
        children[:] = []
        continue
    seen.add(identity)
    children[:] = [
        name for name in children if os.path.join(directory, name) not in skip
    ]
    directories += 1
    # A deadline is reported as incomplete, never as proof that assets are absent.
    if time.monotonic() - started > 1800:
        break
    for name in ("config.json", "adapter_config.json"):
        if name not in files:
            continue
        path = Path(directory) / name
        try:
            if path.stat().st_size > 1024**2:
                continue
            config = json.loads(path.read_text())
            if not isinstance(config, dict):
                continue
            if not any(
                k in config for k in ["model_type", "architectures", "peft_type"]
            ):
                continue
            weights = [
                p
                for p in Path(directory).iterdir()
                if p.is_file() and p.suffix in [".safetensors", ".bin", ".pt", ".gguf"]
            ]
            results.append(
                {
                    "path": str(path.parent),
                    "model_type": config.get("model_type"),
                    "architectures": config.get("architectures"),
                    "peft_type": config.get("peft_type"),
                    "base_model": config.get("base_model_name_or_path"),
                    "weight_files": len(weights),
                    "weight_bytes": sum(p.stat().st_size for p in weights),
                }
            )
        except (OSError, ValueError, UnicodeError):
            errors.append(str(path))
else:
    print(
        json.dumps(
            {
                "complete": True,
                "directories": directories,
                "elapsed": time.monotonic() - started,
                "skipped_roots": sorted(skip),
                "unreadable_count": len(errors),
                "models": results,
            },
            indent=2,
        )
    )
    raise SystemExit(0)
print(
    json.dumps(
        {
            "complete": False,
            "directories": directories,
            "elapsed": time.monotonic() - started,
            "unreadable_count": len(errors),
            "models": results,
        },
        indent=2,
    )
)
raise SystemExit(2)
