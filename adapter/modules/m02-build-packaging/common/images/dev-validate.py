#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify runtime ancestry, tools and full editable imports in a dev container."""

import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build = json.loads((args.build / "image.json").read_text())
    container = json.loads(
        subprocess.check_output(["docker", "inspect", args.container])
    )[0]
    if container["Image"] != build["id"]:
        raise RuntimeError("Validation container uses a different image")
    images = json.loads(
        subprocess.check_output(
            ["docker", "image", "inspect", build["runtime_id"], build["id"]]
        )
    )
    parent_layers = images[0]["RootFS"]["Layers"]
    if images[1]["RootFS"]["Layers"][: len(parent_layers)] != parent_layers:
        raise RuntimeError("Dev image does not inherit the recorded runtime layers")
    command = ["docker", "exec", args.container, "/opt/dynamo/bin/entrypoint.sh"]
    subprocess.run([*command, "python", "/opt/dynamo/bin/dev-check.py"], check=True)
    probe = """
import hashlib, importlib, importlib.metadata as m, json, pathlib, subprocess
imports={}
hashes={}
for name in ('dynamo._core','dynamo.common.platform','kvbm._core','gpu_memory_service'):
    module=importlib.import_module(name)
    required={'dynamo._core':'DistributedRuntime','kvbm._core':'BlockManager'}.get(name)
    if required and not hasattr(module, required):
        raise RuntimeError(f'Wrong Rust binding installed: {name} lacks {required}')
    path=str(pathlib.Path(module.__file__).resolve())
    if not path.startswith('/workspace/dynamo/'):
        raise RuntimeError(f'Not a source import: {name}: {path}')
    imports[name]=path
    with open(path, 'rb') as stream:
        hashes[name]=hashlib.file_digest(stream, 'sha256').hexdigest()
editable={}
for name in ('ai-dynamo-runtime','ai-dynamo','kvbm','gpu-memory-service'):
    info=json.loads(m.distribution(name).read_text('direct_url.json') or '{}')
    if not info.get('dir_info',{}).get('editable'):
        raise RuntimeError(f'Not editable: {name}')
    editable[name]=info
versions={tool:subprocess.check_output([tool,'--version'],text=True).splitlines()[0]
          for tool in ('rustc','cargo','maturin','gdb','strace')}
import debugpy, pytest_timeout
manifest=pathlib.Path('/workspace/dynamo/SOURCE.json')
print(json.dumps(dict(imports=imports, module_sha256=hashes, editable=editable, tools=versions,
    source_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None)))
"""
    result = json.loads(subprocess.check_output([*command, "python", "-c", probe]))
    subprocess.run(
        [*command, "python", "-m", "dynamo.frontend", "--help"],
        stdout=subprocess.DEVNULL,
        check=True,
    )
    result.update(
        status="passed",
        image=build,
        mounts=container["Mounts"],
        runtime_layers_preserved=True,
        runtime_versions_preserved=True,
        frontend_help=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    args.output.with_name(args.output.name + ".license").write_text(
        "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\n"
        "SPDX-License-Identifier: Apache-2.0\n"
    )
    print(json.dumps({"status": "passed", "image": build["id"]}))


if __name__ == "__main__":
    main()
