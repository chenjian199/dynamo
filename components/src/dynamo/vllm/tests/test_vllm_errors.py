# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request errors retain their status without hiding engine failures."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from vllm import exceptions as vllm_exceptions
from vllm.exceptions import VLLMNotFoundError, VLLMValidationError

from dynamo.llm.exceptions import HttpError
from dynamo.vllm import errors
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
    ]
    + (
        [(vllm_exceptions.VLLMUnprocessableEntityError, 422)]
        if hasattr(vllm_exceptions, "VLLMUnprocessableEntityError")
        else []
    ),
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


@pytest.mark.parametrize("generation", ["0.23", "0.26", "client_base"])
def test_exception_api_generations(monkeypatch, generation):
    """Missing optional error classes must not break text-worker imports."""
    fake = ModuleType("vllm.exceptions")
    base = type("VLLMClientError", (Exception,), {})
    if generation == "client_base":
        fake.VLLMClientError = base
    for name in ("VLLMValidationError", "VLLMNotFoundError"):
        setattr(fake, name, type(name, (base,), {}))
    if generation != "0.23":
        fake.VLLMUnprocessableEntityError = type("Unprocessable", (base,), {})
    monkeypatch.setitem(sys.modules, "vllm.exceptions", fake)
    monkeypatch.setattr(sys.modules["vllm"], "exceptions", fake)
    spec = importlib.util.spec_from_file_location(
        "isolated_errors", Path(errors.__file__)
    )
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    for name, status in (
        ("VLLMValidationError", 400),
        ("VLLMNotFoundError", 404),
        ("VLLMUnprocessableEntityError", 422),
    ):
        if hasattr(fake, name):
            error = getattr(fake, name)("bad request")
            assert isinstance(error, isolated.VLLM_CLIENT_ERRORS)
            assert isolated.vllm_client_error_to_http_error(error).code == status
    assert not isinstance(ValueError("engine failure"), isolated.VLLM_CLIENT_ERRORS)
    assert not isinstance(RuntimeError("engine failure"), isolated.VLLM_CLIENT_ERRORS)
    if generation == "client_base":
        assert isinstance(base("future client error"), isolated.VLLM_CLIENT_ERRORS)
