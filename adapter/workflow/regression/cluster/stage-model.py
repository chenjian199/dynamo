#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded private-network transfer of existing model files to a new host directory.

Run serve on the source and fetch on the target over authorized SSH sessions.
The token file is local to each invocation; do not commit it or bind publicly.
"""

import argparse
import functools
import hashlib
import json
import os
import shutil
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def serve(args):
    root = args.path.resolve()
    files = {
        str(p.relative_to(root)): p
        for p in root.rglob("*")
        if p.is_file()
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    }
    assert "config.json" in files
    metadata = [{"name": n, "size": p.stat().st_size} for n, p in sorted(files.items())]
    prefix = "/" + args.token_file.read_text().strip() + "/"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *values):
            pass

        def do_GET(self):
            if not self.path.startswith(prefix):
                self.send_error(404)
                return
            name = urllib.parse.unquote(self.path[len(prefix) :])
            if name == "manifest":
                data = json.dumps(metadata).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif name in files:
                self.send_response(200)
                self.send_header("Content-Length", str(files[name].stat().st_size))
                self.end_headers()
                with files[name].open("rb") as source:
                    shutil.copyfileobj(source, self.wfile, 4 * 1024**2)
            else:
                self.send_error(404)

    server = ThreadingHTTPServer((args.address, args.port), Handler)
    timer = threading.Timer(args.seconds, server.shutdown)
    timer.daemon = True
    timer.start()
    print(
        json.dumps(
            {
                "serving": str(root),
                "files": len(files),
                "bytes": sum(m["size"] for m in metadata),
                "lifetime_seconds": args.seconds,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        timer.cancel()


def fetch(args):
    token = args.token_file.read_text().strip()
    url = f"http://{args.address}:{args.port}/{token}/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url + "manifest", timeout=30) as response:
        metadata = json.load(response)
    target = args.path.absolute()
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite {target}")
    partial = target.with_name(target.name + ".regression-partial")
    partial.mkdir(parents=True, exist_ok=False)
    total = sum(m["size"] for m in metadata)
    if shutil.disk_usage(partial).free < total + 1024**3:
        raise OSError("Insufficient local disk space")
    results = []
    for item in metadata:
        name = Path(item["name"])
        if name.is_absolute() or ".." in name.parts:
            raise ValueError("Invalid model file path")
        path = partial / name
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with opener.open(
            url + urllib.parse.quote(str(name)), timeout=120
        ) as source, path.open("xb") as dest:
            for chunk in iter(functools.partial(source.read, 4 * 1024**2), b""):
                dest.write(chunk)
                digest.update(chunk)
        assert path.stat().st_size == item["size"], name
        results.append({**item, "sha256": digest.hexdigest()})
        print(json.dumps({"copied": str(name), "bytes": item["size"]}), flush=True)
    # Dynamo includes non-weight siblings in the model deployment checksum.
    # Keep transfer evidence outside the model directory so replicas agree.
    manifest = target.with_name(target.name + ".regression-copy.json")
    with manifest.open("x") as output:
        output.write(json.dumps(results, indent=2) + "\n")
    os.rename(partial, target)
    print(json.dumps({"completed": str(target), "bytes": total}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["serve", "fetch"])
    p.add_argument("--path", type=Path, required=True)
    p.add_argument("--address", required=True)
    p.add_argument("--port", type=int, default=29280)
    p.add_argument("--token-file", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=1800)
    a = p.parse_args()
    (serve if a.mode == "serve" else fetch)(a)
