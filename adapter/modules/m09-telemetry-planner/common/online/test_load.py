# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Guard the distinction between token delivery and visible-text latency."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "m09_load", Path(__file__).with_name("load.py")
)
load = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load)
pytestmark = [pytest.mark.unit, pytest.mark.gpu_0, pytest.mark.pre_merge]


class Response:
    status = 200

    def __init__(self, usage):
        self.usage = usage
        self.content = self.stream()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def stream(self):
        for token in [126, 229, 1010]:
            yield (
                "data: "
                + json.dumps(
                    {
                        "choices": [{"text": "", "index": 0}],
                        "nvext": {"completion_token_ids": [token]},
                    }
                )
                + "\n"
            ).encode()
        yield (
            "data: "
            + json.dumps(
                {
                    "choices": [{"text": "", "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 1024, "completion_tokens": self.usage},
                }
            )
            + "\n"
        ).encode()
        yield b"data: [DONE]\n"


@pytest.mark.parametrize(
    "token_metrics,usage,valid", [(True, 3, True), (True, 4, False), (False, 3, False)]
)
def test_empty_visible_output_requires_complete_token_evidence(
    token_metrics, usage, valid
):
    args = SimpleNamespace(
        model="model",
        isl=1024,
        osl=3,
        token_id_metrics=token_metrics,
        url="http://test",
    )
    session = SimpleNamespace(post=lambda *a, **kw: Response(usage))
    if valid:
        result = asyncio.run(load.request(session, args, 1))
        assert result["visible_characters"] == 0
        assert result["completion_tokens"] == 3
        assert result["ttft_ms"] >= 0
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(load.request(session, args, 1))
