# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the installed vLLM MultiConnector delegation contract without devices."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = [pytest.mark.pre_merge, pytest.mark.gpu_0, pytest.mark.unit]


@pytest.fixture(autouse=True)
def native_kvbm_import(monkeypatch):
    monkeypatch.setenv("DYN_KVBM_TRANSFER_BACKEND", "platform")


def test_pd_metadata_binds_each_child_once():
    from kvbm.vllm_integration.connector.pd_connector import (
        PdConnector,
        PdConnectorMetadata,
    )

    connector = object.__new__(PdConnector)
    connector._connectors = [Mock(), Mock()]
    connector._extra_async_saves = {}
    child_metadata = (Mock(), Mock())
    metadata = PdConnectorMetadata(metadata=child_metadata)
    connector.bind_connector_metadata(metadata)
    assert connector.has_connector_metadata()
    for child, value in zip(connector._connectors, child_metadata):
        child.bind_connector_metadata.assert_called_once_with(value)
    connector.clear_connector_metadata()
    assert not connector.has_connector_metadata()
    for child in connector._connectors:
        child.clear_connector_metadata.assert_called_once_with()


def test_pd_without_prometheus_exporters_keeps_statistics_optional(monkeypatch):
    from kvbm.vllm_integration.connector.pd_connector import PdConnector
    from prometheus_client import Counter, Gauge, Histogram

    class NoExporter:
        @classmethod
        def build_prom_metrics(cls, *args):
            return None

    config = SimpleNamespace(kv_transfer_config=Mock())
    monkeypatch.setattr(
        PdConnector,
        "_get_connector_classes_and_configs",
        lambda _: [(NoExporter, config)],
    )
    metrics = PdConnector.build_prom_metrics(
        config, {c: c for c in (Counter, Gauge, Histogram)}, [], {}
    )
    assert metrics is None
