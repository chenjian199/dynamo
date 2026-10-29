#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate selected local model indexes and fingerprint configuration/tokenizer files."""

import hashlib
import json
import sys
from pathlib import Path

rows = []
for value in sys.argv[1:]:
    path = Path(value)
    config = json.loads((path / "config.json").read_text())
    indexes = {}
    for index in path.glob("*.index.json"):
        data = json.loads(index.read_text())
        if "weight_map" not in data:
            continue
        names = sorted(set(data["weight_map"].values()))
        assert all(
            (path / name).is_file() and (path / name).stat().st_size > 0
            for name in names
        ), index
        indexes[index.name] = {
            "shards": len(names),
            "bytes": sum((path / n).stat().st_size for n in names),
        }
    files = {}
    for name in (
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "preprocessor_config.json",
        "chat_template.jinja",
    ):
        p = path / name
        if p.is_file():
            files[name] = {
                "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
    rows.append(
        {
            "path": str(path),
            "model_type": config.get("model_type"),
            "architectures": config.get("architectures"),
            "quantization_config": config.get("quantization_config"),
            "indexes": indexes,
            "metadata": files,
            "qualification": "Indexes checked; metadata hashed. Full weight content hashes are not computed here.",
        }
    )
print(json.dumps(rows, indent=2))
