#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Record the frozen standalone encoder loader's model family and capacity prerequisites."""
import inspect
import json
from pathlib import Path

from dynamo.vllm.multimodal_utils import model

path = "/models/candidate"
config = json.loads(Path(path, "config.json").read_text())
family = model.resolve_model_family(path)
source = inspect.getsource(model.load_vision_model)
print(
    "REGRESSION_RESULT="
    + json.dumps(
        {
            "status": "preflight_only",
            "model_architectures": config.get("architectures"),
            "resolved_family": str(family) if family else None,
            "loader_source": source,
            "qualification": "No model weights were loaded and no encoder inference PASS is claimed",
        }
    ),
    flush=True,
)
