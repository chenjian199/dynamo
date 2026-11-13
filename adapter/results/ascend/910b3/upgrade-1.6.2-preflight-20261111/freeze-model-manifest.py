# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Record existing model bytes without downloading or changing model inputs."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
model_id = sys.argv[2]
files = {}
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.suffix not in {".json", ".safetensors", ".bin", ".model", ".txt", ".py", ".tiktoken"}:
        continue
    if "manifest" in path.name or path.name.startswith("."):
        continue
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    files[str(path.relative_to(root))] = digest.hexdigest()
assert "config.json" in files and any(p.endswith((".safetensors", ".bin")) for p in files)
revision = "sha256:" + hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
data = {"model_id": model_id, "revision": revision, "files": files}
content = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
manifest = root / ("heyi-upgrade-162-manifest-" + revision[7:19] + ".json")
if '--read-only' in sys.argv[3:]:
    pass
elif manifest.exists():
    assert manifest.read_bytes() == content, "Existing manifest differs; refusing overwrite"
else:
    with manifest.open("xb") as stream:
        stream.write(content)
print(json.dumps({"id": model_id, "path": str(root), "revision": revision,
                  "manifest": manifest.name, "manifest_sha256": hashlib.sha256(content).hexdigest(),
                  "file_count": len(files), "files": files}, indent=2))
