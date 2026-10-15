# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request errors retain their status without hiding engine failures."""

import pytest
from vllm.exceptions import (
    VLLMNotFoundError,
    VLLMUnprocessableEntityError,
    VLLMValidationError,
)

from dynamo.llm.exceptions import HttpError
from dynamo.vllm.handlers import _translate_vllm_client_errors

pytestmark = [
    pytest.mark.unit,
    pytest.mark.vllm,
    pytest.mark.core,
    pytest.mark.gpu_0,
    pytest.mark.profiled_vram_gib(0),
    pytest.mark.timeout(180),
    pytest.mark.pre_merge,
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "status"),
    [
        (VLLMValidationError, 400),
        (VLLMNotFoundError, 404),
        (VLLMUnprocessableEntityError, 422),
    ],
)
async def test_request_errors_preserve_status_after_partial_output(error_type, status):
    error = error_type("invalid request")

    async def generate():
        yield "first token"
        raise error

    stream = _translate_vllm_client_errors(generate())
    assert await anext(stream) == "first token"
    with pytest.raises(HttpError) as caught:
        await anext(stream)
    assert caught.value.code == status
    assert caught.value.__cause__ is error


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
async def test_unrelated_engine_errors_are_not_mapped_to_client_errors(error_type):
    error = error_type("engine failure")

    async def generate():
        yield "first token"
        raise error

    stream = _translate_vllm_client_errors(generate())
    assert await anext(stream) == "first token"
    with pytest.raises(error_type) as caught:
        await anext(stream)
    assert caught.value is error


@pytest.mark.asyncio
async def test_normal_generation_is_unchanged():
    async def generate():
        yield "first"
        yield "second"

    assert [chunk async for chunk in _translate_vllm_client_errors(generate())] == [
        "first",
        "second",
    ]
