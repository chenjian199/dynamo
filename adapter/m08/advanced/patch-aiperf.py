#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Guarded AIPerf 0.13.0 fix for offline tokenizers supplied by local hostPath."""

import importlib.metadata
import importlib.util
from pathlib import Path

if importlib.metadata.version("aiperf") != "0.13.0":
    raise RuntimeError("Revalidate this patch before changing AIPerf versions")
path = Path(importlib.util.find_spec("aiperf.common.tokenizer").origin)
source = path.read_text()
anchor = """        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError

        candidates = [name]
"""
replacement = """        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError

        # HEYI: a local directory is not a Hugging Face repository ID.
        if Path(name).is_dir():
            return str(Path(name).resolve())

        candidates = [name]
"""
if source.count(anchor) != 1:
    raise RuntimeError("AIPerf source changed or patch already applied")
source = source.replace(anchor, replacement)
source = source.replace(
    "# SPDX-License-Identifier: Apache-2.0",
    "# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\n"
    "# SPDX-License-Identifier: Apache-2.0\n"
    "# Derived from NVIDIA AIPerf. Modified by HEYI.",
    1,
)
path.write_text(source)
print("Applied offline local-path tokenizer fix:", path)
