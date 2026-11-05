# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""API tool boundary and mocked Responses continuation; no paid network calls."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapter.workflow.harnesses.agent import responses
from adapter.workflow.harnesses.workspace_tools import WorkspaceTools


class ApiToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src/a.py").write_text("before\n")
        self.tools = WorkspaceTools(self.root, ["src"], ["AGENTS.md"])

    def test_read_write_preimage_and_scope(self):
        result = self.tools.call("read_file", {"path": "src/a.py", "start_line": 1})
        self.tools.call(
            "write_file",
            {
                "path": "src/a.py",
                "expected_sha256": result["sha256"],
                "content": "after\n",
            },
        )
        self.assertEqual((self.root / "src/a.py").read_text(), "after\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.tools.call(
                "write_file",
                {
                    "path": "src/a.py",
                    "expected_sha256": result["sha256"],
                    "content": "bad",
                },
            )
        with self.assertRaisesRegex(ValueError, "scope"):
            self.tools.call(
                "write_file",
                {"path": "private", "expected_sha256": "", "content": "bad"},
            )

    def test_parent_and_symlink_escape(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.tools.call("read_file", {"path": "../secret", "start_line": 1})
        (self.root / "src/link").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.tools.call("read_file", {"path": "src/link", "start_line": 1})

    def test_responses_tools_loop(self):
        outputs = [
            {
                "id": "one",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "read-1",
                        "name": "read_file",
                        "arguments": json.dumps({"path": "src/a.py", "start_line": 1}),
                    }
                ],
            },
            {
                "id": "two",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "feature_id": "M01-F01",
                                        "summary": "inspected",
                                        "status": "proposed",
                                        "blockers": [],
                                        "changed_files": [],
                                        "tasks_json": "[]",
                                    }
                                ),
                            }
                        ],
                    }
                ],
            },
        ]
        requests = []

        def response(req, timeout):
            requests.append(json.loads(req.data))
            return io.BytesIO(json.dumps(outputs.pop(0)).encode())

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key"}),
            patch("urllib.request.urlopen", side_effect=response),
        ):
            result, session = responses(
                "fixture", "explicit-model", workspace=self.tools
            )
        self.assertEqual(session, "two")
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(requests[1]["input"][-1]["type"], "function_call_output")
        self.assertNotIn("fixture-key", json.dumps(requests))
        self.assertFalse(requests[0]["parallel_tool_calls"])

    def test_refusal_or_incomplete_is_not_success(self):
        payload = {"status": "incomplete", "output": []}
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key"}),
            patch(
                "urllib.request.urlopen",
                return_value=io.BytesIO(json.dumps(payload).encode()),
            ),
            self.assertRaisesRegex(ValueError, "incomplete"),
        ):
            responses("fixture", "explicit-model", workspace=self.tools)
