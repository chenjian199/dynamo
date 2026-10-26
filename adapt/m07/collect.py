#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect M07 evidence without changing test verdicts or historical source hashes."""

import argparse
import hashlib
import json
import re
from pathlib import Path

LICENSE = (
    "SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.\n"
    "SPDX-License-Identifier: Apache-2.0\n"
)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".txt":
        # Keep raw logs in the run directory; repository copies satisfy whitespace hooks.
        data = b"\n".join(line.rstrip() for line in data.splitlines()).rstrip() + b"\n"
    path.write_bytes(data)
    Path(str(path) + ".license").write_text(LICENSE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument(
        "--platform", choices=["cuda", "metax", "ascend"], required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-prefix", help="select this prefix for every suite")
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="raw is one probe directory; no engine suites",
    )
    parser.add_argument(
        "--suite-dir", action="append", default=[], help="suite=raw-directory override"
    )
    args = parser.parse_args()
    overrides = dict(item.split("=", 1) for item in args.suite_dir)
    root = Path(__file__).resolve().parents[2]
    summary = {"platform": args.platform, "suites": {}, "probe": {}}
    for suite in (
        () if args.probe_only else ("unit", "media", "gms", "legacy", "encoder")
    ):
        if args.platform == "ascend" and suite in ("gms", "legacy"):
            summary["suites"][suite] = {
                "status": "blocked",
                "reason": (
                    "Ascend GMS provider unavailable: read-only VMM and sharing "
                    "contract not verified; see probe evidence"
                ),
            }
            continue
        prefix = args.run_prefix or (
            "closed" if suite in ("unit", "encoder") else "verified"
        )
        directory = args.raw / overrides.get(suite, f"{prefix}-{suite}")
        report = json.loads((directory / "result.json").read_text())
        summary["suites"][suite] = {
            "passed": report["passed"],
            "raw": str(directory),
            "full_suite_log_sha256": hashlib.sha256(
                (directory / "m07.log.txt").read_bytes()
            ).hexdigest(),
            "source_changed_since_run": [
                name
                for name, digest in report["source_sha256"].items()
                if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest
            ],
        }
        if suite == "encoder":
            summary["suites"][suite]["remaining_embedding_files"] = report.get(
                "remaining_embedding_files"
            )
        for name in ("result.json", "m07.log.txt"):
            write(args.output / suite / name, (directory / name).read_bytes())
        if suite == "encoder":
            for path in sorted((directory / "case").glob("*.json")):
                write(args.output / suite / path.name, path.read_bytes())
            log = (directory / "case/server.log").read_bytes()
            summary["suites"][suite]["full_server_log_sha256"] = hashlib.sha256(
                log
            ).hexdigest()
            lines = re.sub(
                r"\x1b\[[0-9;]*m", "", log.decode(errors="replace")
            ).splitlines()
            excerpt = [
                line
                for line in lines
                if any(
                    term in line
                    for term in (
                        "Starting to serve the encode",
                        "Connected to encode workers",
                        "encoder initialization",
                        "Model loading took",
                        "GPU KV cache size",
                        "request completed",
                        "Error processing request",
                    )
                )
            ]
            write(
                args.output / suite / "server-excerpt.txt",
                ("\n".join(excerpt) + "\n").encode(),
            )
    probe = args.raw if args.probe_only else args.raw / "verified-probe"
    logs = sorted(probe.glob("mode-*.txt"))
    if args.probe_only and not logs:
        raise FileNotFoundError(f"No probe logs in {probe}")
    for path in logs:
        log = path.read_bytes()
        status = int(path.with_suffix(".exit").read_text())
        summary["probe"][path.stem] = {
            "returncode": status,
            "raw_log": str(path),
            "full_log_sha256": hashlib.sha256(log).hexdigest(),
        }
        lines = log.decode(errors="replace").splitlines()
        excerpt = [
            line
            for line in lines
            if any(
                term in line
                for term in (
                    " rc=",
                    "_rc=",
                    "granularity=",
                    "read_access=",
                    "posix_fd=",
                    "probe_error=",
                    "exported_handle_type=",
                    "access_sequence=",
                    "Not support type",
                    "halMemSetAccess",
                    "halMemGetAccess",
                )
            )
        ]
        write(args.output / "probe" / path.name, ("\n".join(excerpt) + "\n").encode())
    if args.probe_only:
        environment = (probe / "environment.json").read_bytes()
        if json.loads(environment)["platform"] != args.platform:
            raise ValueError("Probe environment platform does not match --platform")
        write(args.output / "environment.json", environment)
        write(
            args.output / "summary.json",
            (json.dumps(summary, indent=2) + "\n").encode(),
        )
        return
    source = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (root / "adapt/m07/sources.txt").read_text().splitlines()
    }
    write(args.output / "source.json", (json.dumps(source, indent=2) + "\n").encode())
    write(args.output / "summary.json", (json.dumps(summary, indent=2) + "\n").encode())


if __name__ == "__main__":
    main()
