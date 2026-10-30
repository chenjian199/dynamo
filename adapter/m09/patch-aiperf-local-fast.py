#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Use the explicitly declared generic fast tokenizer without loading model config.

Apply to an isolated tools Pod overlay, not the inference engine or shared M08 CM.
Unknown classes and remote tokenizer names retain AIPerf's original loader.
"""
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--input", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
original = a.input.read_text()
start = original.index("    def _load_from_hub(")
end = original.index("    @classmethod\n    def _build_with_kwargs", start)
block = original[start:end]
needle = "        from transformers import AutoTokenizer\n"
if block.count(needle) != 1 or block.count("AutoTokenizer.from_pretrained") != 3:
    raise SystemExit("Unrecognized AIPerf loader; inspect before applying")
block = block.replace("AutoTokenizer.from_pretrained", "loader")
block = block.replace(
    needle,
    """        from pathlib import Path
        import json
        from transformers import AutoTokenizer, PreTrainedTokenizerFast

        loader = AutoTokenizer.from_pretrained
        local = Path(name)
        metadata = local / "tokenizer_config.json"
        if metadata.is_file() and (local / "tokenizer.json").is_file():
            declared = json.loads(metadata.read_text()).get("tokenizer_class")
            if declared == "PreTrainedTokenizerFast":
                loader = PreTrainedTokenizerFast.from_pretrained
""",
)
patched = original[:start] + block + original[end:]
notice = "# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\n# Derived from NVIDIA AIPerf. Modified by HEYI.\n"
a.output.write_text(notice + patched)
print(
    json.dumps(
        {
            "input_sha256": hashlib.sha256(a.input.read_bytes()).hexdigest(),
            "output_sha256": hashlib.sha256(a.output.read_bytes()).hexdigest(),
        }
    )
)
