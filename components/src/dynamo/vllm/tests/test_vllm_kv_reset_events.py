# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""An idle engine must publish real invalidation events without another request."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from vllm.distributed.kv_events import AllBlocksCleared, BlockRemoved
from vllm.v1.core.sched.async_scheduler import AsyncScheduler

from dynamo.vllm.instrumented_scheduler import InstrumentedScheduler

pytestmark = [
    pytest.mark.unit,
    pytest.mark.vllm,
    pytest.mark.gpu_0,
    pytest.mark.pre_merge,
]


@pytest.mark.parametrize("reset_result", [True, False])
@pytest.mark.parametrize("with_connector", [True, False])
def test_reset_publishes_pending_events_without_forward_pass(
    monkeypatch, reset_result, with_connector
):
    scheduler = InstrumentedScheduler.__new__(InstrumentedScheduler)
    pending = []
    clear = AllBlocksCleared()
    removed = BlockRemoved(block_hashes=[42], medium="GPU")

    def parent_reset(self, *, reset_running_requests, reset_connector):
        assert self is scheduler
        assert reset_running_requests and reset_connector
        pending.append(clear)
        return reset_result

    monkeypatch.setattr(AsyncScheduler, "reset_prefix_cache", parent_reset)

    def drain():
        events = pending[:]
        pending.clear()
        return events

    scheduler.kv_cache_manager = SimpleNamespace(take_events=drain)
    scheduler.connector = (
        SimpleNamespace(take_events=MagicMock(return_value=iter([removed])))
        if with_connector
        else None
    )
    scheduler.kv_event_publisher = MagicMock()

    assert scheduler.reset_prefix_cache(True, True) is reset_result
    scheduler.kv_event_publisher.publish.assert_called_once()
    batch = scheduler.kv_event_publisher.publish.call_args.args[0]
    assert batch.events == ([clear, removed] if with_connector else [clear])
    assert batch.ts > 0
    assert drain() == []  # A later output step cannot publish the clear twice.


@pytest.mark.parametrize("reset_result", [True, False])
def test_reset_without_events_does_not_invent_invalidation(monkeypatch, reset_result):
    monkeypatch.setattr(
        AsyncScheduler, "reset_prefix_cache", MagicMock(return_value=reset_result)
    )
    scheduler = InstrumentedScheduler.__new__(InstrumentedScheduler)
    scheduler.kv_cache_manager = SimpleNamespace(take_events=lambda: [])
    scheduler.connector = None
    scheduler.kv_event_publisher = MagicMock()
    assert scheduler.reset_prefix_cache() is reset_result
    scheduler.kv_event_publisher.publish.assert_not_called()
