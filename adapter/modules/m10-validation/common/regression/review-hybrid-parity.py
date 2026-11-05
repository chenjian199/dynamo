#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Review native controls without converting failed token parity into a pass."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("aggregate", "pd", "native-repeat", "native-pd", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    paths = {
        name: getattr(args, name.replace("-", "_"))
        for name in ("aggregate", "pd", "native-repeat", "native-pd")
    }
    data = {name: json.loads(path.read_text()) for name, path in paths.items()}

    def dynamo_rows(value):
        assert value["passed"] is True
        rows = value["requests"] + [value["recovery"]]
        assert len(rows) == 12 and value["cancellations"] == 4
        return {row["sequence"]: tuple(row["completion_token_ids"]) for row in rows}

    aggregate = dynamo_rows(data["aggregate"])
    pd = dynamo_rows(data["pd"])
    sequences = set(aggregate)
    assert sequences == set(pd) and len(sequences) == 12
    mismatches = {key for key in sequences if aggregate[key] != pd[key]}
    repeat = data["native-repeat"]
    native = data["native-pd"]
    assert repeat["requests_succeeded"] is native["requests_succeeded"] is True
    assert len(repeat["rounds"]) == 3 and len(native["rounds"]) == 2
    local_tokens = {key: set() for key in sequences}
    native_tokens = {key: set() for key in sequences}
    native_pair_mismatches = set()
    native_local_changes = set()
    for rows in repeat["rounds"]:
        assert len(rows) == 12 and {row["sequence"] for row in rows} == sequences
        for row in rows:
            local_tokens[row["sequence"]].add(tuple(row["completion_token_ids"]))
    for rows in native["rounds"]:
        assert len(rows) == 12 and {row["sequence"] for row in rows} == sequences
        for row in rows:
            key = row["sequence"]
            tokens = {name: tuple(value) for name, value in row["tokens"].items()}
            assert set(tokens) == {"local_before", "pd", "local_after"}
            if len(set(tokens.values())) != 1:
                native_pair_mismatches.add(key)
            if tokens["local_before"] != tokens["local_after"]:
                native_local_changes.add(key)
            native_tokens[key].update(tokens.values())
    local_variability = {key for key, values in local_tokens.items() if len(values) > 1}
    stable_agreement = []
    for key in sorted(sequences - mismatches):
        values = {aggregate[key], pd[key]} | local_tokens[key] | native_tokens[key]
        if len(values) == 1:
            stable_agreement.append(key)
    all_lengths_valid = all(
        len(tokens) == 16
        for key in sequences
        for tokens in (
            {aggregate[key], pd[key]} | local_tokens[key] | native_tokens[key]
        )
    )
    reproduced = bool(mismatches) and (
        mismatches
        == local_variability
        == native_pair_mismatches
        == native_local_changes
        and set(stable_agreement) == sequences - mismatches
        and all_lengths_valid
    )
    report = {
        "scope": "Completeness of the diagnostic comparison; not token-parity certification",
        "passed": reproduced,
        "classification": (
            "native_output_variability_reproduced" if reproduced else "unresolved"
        ),
        "strict_dynamo_token_parity_passed": not mismatches,
        "dynamo_mismatch_sequences": sorted(mismatches),
        "native_aggregate_variable_sequences": sorted(local_variability),
        "native_pd_mismatch_sequences": sorted(native_pair_mismatches),
        "same_native_decode_local_variable_sequences": sorted(native_local_changes),
        "stable_sequences_equal_across_all_controls": stable_agreement,
        "all_completion_lengths_are_16": all_lengths_valid,
        "limitation": "Exact numerical parity is not certified. The observed differences also occur in direct native vLLM and native Hybrid PD without Dynamo; these controls do not locate the precise numerical cause inside the native engine.",
        "evidence": {
            name: {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in paths.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    if not reproduced:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
