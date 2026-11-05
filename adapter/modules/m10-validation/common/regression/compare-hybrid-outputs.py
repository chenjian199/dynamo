#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compare recorded aggregate and hybrid PD token streams without normalizing IDs."""
import argparse
import hashlib
import json
from pathlib import Path


def records(path):
    data = json.loads(path.read_text())
    assert data["passed"] is True, path
    rows = data["requests"] + [data["recovery"]]
    assert len(rows) == 12, path
    result = {}
    for row in rows:
        key = row["sequence"]
        assert key not in result, (path, key)
        assert len(row["completion_token_ids"]) == row["completion_tokens"] == 16
        result[key] = row
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--pd", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    aggregate, pd = records(args.aggregate), records(args.pd)
    assert aggregate.keys() == pd.keys()
    mismatches = []
    for sequence, left in aggregate.items():
        right = pd[sequence]
        assert left["prompt_tokens"] == right["prompt_tokens"]
        if left["completion_token_ids"] != right["completion_token_ids"]:
            mismatches.append(
                {
                    "sequence": sequence,
                    "prompt_tokens": left["prompt_tokens"],
                    "aggregate": left["completion_token_ids"],
                    "pd": right["completion_token_ids"],
                }
            )
    report = {
        "passed": not mismatches,
        "requests_compared": len(aggregate),
        "mismatches": mismatches,
        "inputs": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (args.aggregate, args.pd)
        },
        "scope": "Exact token parity for the recorded synthetic inputs, not model quality",
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
