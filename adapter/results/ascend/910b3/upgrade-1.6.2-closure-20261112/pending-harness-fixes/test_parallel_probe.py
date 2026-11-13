# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test probe rejection and cleanup; these are not hardware qualification."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

source = Path(__file__).parent / "adapter/modules/m03-engine-integration/common/engine/rank-smoke.py"
node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_parallel")


class ParallelTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, wrong_rank=False, missing_token=False, duplicate_uuid=False):
        from unittest.mock import Mock
        import os
        import re
        runtime = Mock()
        client = Mock()
        client.wait_for_instances = AsyncMock()
        client.instances.return_value = [SimpleNamespace(platform={
            "platform_id": "ascend",
            "devices": [{"uuid": "a"}, {"uuid": "a" if duplicate_uuid else "b"}],
        })]
        endpoint = Mock()
        endpoint.client = AsyncMock(return_value=client)
        runtime.endpoint.return_value = endpoint
        seen = []

        async def http(url, payload=None, raw=False):
            if raw:
                return 'dynamo_component_inflight_requests{dynamo_endpoint="generate",worker_id="abc"} 0\n'
            seen.append(payload)
            rank = payload["nvext"]["dp_rank"]
            return {"usage": {"completion_tokens": 1}, "nvext": {
                "worker_id": {"decode_dp_rank": 0 if wrong_rank else rank},
                "completion_token_ids": [] if missing_token else [33],
            }}

        namespace = {"os": os, "re": re, "asyncio": asyncio,
                     "DistributedRuntime": Mock(return_value=runtime), "http": http}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        report = {}
        try:
            await namespace["run_parallel"](SimpleNamespace(platform="ascend", dp=2), report)
        finally:
            runtime.shutdown.assert_called_once()
        return report, seen

    async def test_every_rank_serves(self):
        report, seen = await self.execute()
        self.assertTrue(report["passed"])
        self.assertEqual([r["nvext"]["dp_rank"] for r in seen], [0, 1])
        self.assertEqual(len(report["responses"]), 2)

    async def test_misrouting_is_rejected(self):
        with self.assertRaises(AssertionError):
            await self.execute(wrong_rank=True)

    async def test_missing_generation_is_rejected(self):
        with self.assertRaises(AssertionError):
            await self.execute(missing_token=True)

    async def test_duplicate_physical_device_is_rejected(self):
        with self.assertRaises(AssertionError):
            await self.execute(duplicate_uuid=True)


if __name__ == "__main__":
    unittest.main()
