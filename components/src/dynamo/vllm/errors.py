# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Derived from NVIDIA Dynamo. Modified by HEYI.

"""Translate vLLM request errors into Dynamo's HTTP error boundary."""

from vllm.exceptions import (
    VLLMNotFoundError,
    VLLMUnprocessableEntityError,
    VLLMValidationError,
)

from dynamo.llm.exceptions import HttpError

# vLLM 0.26 (including MetaX) predates the common client-error base class.
# Match its concrete request errors, never arbitrary ValueError/Exception.
VLLM_CLIENT_ERRORS: tuple[type[Exception], ...]
try:
    from vllm.exceptions import VLLMClientError
except ImportError:
    VLLM_CLIENT_ERRORS = (
        VLLMValidationError,
        VLLMNotFoundError,
        VLLMUnprocessableEntityError,
    )
else:
    VLLM_CLIENT_ERRORS = (VLLMClientError,)


def vllm_client_error_to_http_error(exc: Exception) -> HttpError:
    """Preserve the HTTP status assigned by vLLM's client-error hierarchy."""
    if isinstance(exc, VLLMUnprocessableEntityError):
        status_code = 422
    elif isinstance(exc, VLLMNotFoundError):
        status_code = 404
    else:
        status_code = 400
    return HttpError(status_code, str(exc))
