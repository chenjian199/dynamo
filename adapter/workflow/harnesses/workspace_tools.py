# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded file tools for an API agent; no arbitrary shell or credential access."""

import hashlib
import os
import tempfile
from pathlib import Path

from adapter.workflow.core import digest


def function(name, description, properties):
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOLS = [
    function(
        "list_files",
        "List files under one declared readable path.",
        {"path": {"type": "string"}},
    ),
    function(
        "read_file",
        "Read up to 300 lines and get the complete file SHA256.",
        {"path": {"type": "string"}, "start_line": {"type": "integer"}},
    ),
    function(
        "write_file",
        "Replace a scoped source file only if its SHA256 matches; empty SHA means a new file.",
        {
            "path": {"type": "string"},
            "expected_sha256": {"type": "string"},
            "content": {"type": "string"},
        },
    ),
]


class WorkspaceTools:
    def __init__(self, root, write_paths, read_paths):
        self.root = Path(root).resolve()
        self.write_paths = [self.resolve(p) for p in write_paths]
        self.read_paths = self.write_paths + [self.resolve(p) for p in read_paths]

    def resolve(self, name):
        value = (self.root / name).resolve()
        if not value.is_relative_to(self.root):
            raise ValueError("Path escapes the workspace")
        return value

    def allowed(self, name, write=False):
        path = self.resolve(name)
        roots = self.write_paths if write else self.read_paths
        if not any(path == root or path.is_relative_to(root) for root in roots):
            raise ValueError("Path is outside the declared feature scope")
        return path

    def call(self, name, arguments):
        if name == "list_files":
            p = self.allowed(arguments["path"])
            files = [p] if p.is_file() else sorted(p.rglob("*"))
            safe = [
                str(f.relative_to(self.root))
                for f in files
                if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts
            ]
            return {"files": safe[:500], "truncated": len(safe) > 500}
        if name == "read_file":
            p = self.allowed(arguments["path"])
            if p.stat().st_size > 1024 * 1024:
                raise ValueError("Read limit: 1 MiB per source file")
            start = arguments["start_line"]
            if start < 1:
                raise ValueError("start_line must be positive")
            lines = p.read_text().splitlines()
            return {
                "sha256": digest(p),
                "total_lines": len(lines),
                "start_line": start,
                "text": "\n".join(lines[start - 1 : start + 299]),
            }
        if name == "write_file":
            p = self.allowed(arguments["path"], write=True)
            actual = digest(p) if p.exists() else ""
            if actual != arguments["expected_sha256"]:
                raise ValueError("File changed; read again before editing")
            data = arguments["content"].encode()
            if len(data) > 1024 * 1024:
                raise ValueError("Write limit: 1 MiB")
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".agent-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                if p.exists():
                    os.chmod(tmp, p.stat().st_mode)
                os.replace(tmp, p)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return {
                "path": str(p.relative_to(self.root)),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        raise ValueError("Unknown workspace tool")
