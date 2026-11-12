# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CPU checks for handoff probe verdicts, not transport hardware qualification."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[1] / "handoff.py"
    spec = importlib.util.spec_from_file_location("handoff_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Item:
    def __init__(self, value):
        self.value = value

    def data(self):
        return self.value


class Client:
    def __init__(self, outputs):
        self.outputs = outputs
        self.requests = []

    async def wait_for_instances(self):
        pass

    async def generate(self, request):
        self.requests.append(copy.deepcopy(request))

        async def stream():
            for value in self.outputs:
                yield Item(value)

        return stream()

    async def client(self):
        return self


class Runtime:
    def __init__(self, decoded):
        self.handoff = {
            "disaggregated_params": {
                "kv_transfer_params": {
                    "_dynamo_pd_contract": {"block_size": 128},
                    "remote_bootstrap_addr": "http://producer:8998",
                    "remote_engine_id": "original",
                }
            }
        }
        self.producer = Client([self.handoff])
        self.consumer = Client(decoded)
        self.closed = False

    def endpoint(self, name):
        return self.producer if ".prefill." in name else self.consumer

    def shutdown(self):
        self.closed = True


def execute(probe, tmp_path, case, decoded, *extra):
    args = probe.parse_args(
        [
            "--namespace",
            "test",
            "--case",
            case,
            "--output",
            str(tmp_path / "result.json"),
            *extra,
        ]
    )
    runtime = Runtime(decoded)
    calls = []

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return runtime

    asyncio.run(probe.run(args, factory))
    return runtime, calls, json.loads(args.output.read_text())


def test_valid_kubernetes_handoff_preserves_metadata(probe, tmp_path):
    token_file = tmp_path / "tokens.json"
    token_file.write_text("[4,5,6]")
    runtime, calls, result = execute(
        probe,
        tmp_path,
        "valid",
        [
            {"token_ids": [7], "finish_reason": None},
            {"token_ids": [8], "finish_reason": "length"},
        ],
        "--discovery-backend",
        "kubernetes",
        "--model",
        "custom-model",
        "--token-ids",
        str(token_file),
        "--max-tokens",
        "2",
    )
    assert calls[0][0][1] == "kubernetes"
    assert runtime.producer.requests[0]["model"] == "custom-model"
    assert runtime.producer.requests[0]["token_ids"] == [4, 5, 6]
    assert result["output_token_ids"] == [7, 8]
    assert result["prefill_handoff"] == result["decode_handoff"] == runtime.handoff
    assert result["status"] == "passed" and runtime.closed


@pytest.mark.parametrize(
    "decoded",
    [
        [],
        [{"token_ids": [7]}],
        [{"token_ids": [7], "finish_reason": "error"}],
        [{"token_ids": [], "finish_reason": "stop"}],
    ],
)
def test_empty_error_or_incomplete_stream_cannot_pass(probe, tmp_path, decoded):
    with pytest.raises(RuntimeError, match="did not complete"):
        execute(probe, tmp_path, "valid", decoded)
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["status"] == "failed"


def test_fault_copy_does_not_overwrite_original_handoff(probe, tmp_path):
    runtime, _, result = execute(
        probe,
        tmp_path,
        "mismatch",
        [{"finish_reason": "error", "error": "Incompatible PD peer: block_size"}],
    )
    original = result["prefill_handoff"]["disaggregated_params"]["kv_transfer_params"]
    changed = result["decode_handoff"]["disaggregated_params"]["kv_transfer_params"]
    assert original["_dynamo_pd_contract"]["block_size"] == 128
    assert changed["_dynamo_pd_contract"]["block_size"] == 129
    assert result["prefill_handoff"] == runtime.handoff
    assert runtime.closed


def test_unreachable_requires_explicit_error(probe, tmp_path):
    with pytest.raises(RuntimeError, match="explicit backend error"):
        execute(
            probe,
            tmp_path,
            "unreachable",
            [{"token_ids": [7], "finish_reason": "length"}],
            "--unreachable-port",
            "1",
        )


def test_invalid_token_file_closes_runtime(probe, tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text("[1, true]")
    args = probe.parse_args(
        [
            "--namespace",
            "test",
            "--case",
            "valid",
            "--output",
            str(tmp_path / "result.json"),
            "--token-ids",
            str(path),
        ]
    )
    runtime = Runtime([])
    with pytest.raises(ValueError, match="token_ids"):
        asyncio.run(probe.run(args, lambda *args, **kwargs: runtime))
    assert runtime.closed and not runtime.producer.requests


def test_unreachable_requires_port(probe, tmp_path):
    with pytest.raises(SystemExit):
        probe.parse_args(
            [
                "--namespace",
                "test",
                "--case",
                "unreachable",
                "--output",
                str(tmp_path / "result.json"),
            ]
        )


def test_success_message_cannot_satisfy_negative_case(probe, tmp_path):
    with pytest.raises(RuntimeError, match="Mismatch not rejected"):
        execute(
            probe,
            tmp_path,
            "mismatch",
            [
                {
                    "token_ids": [7],
                    "finish_reason": "length",
                    "text": "Incompatible PD peer",
                }
            ],
        )
