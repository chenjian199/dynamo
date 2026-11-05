# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CPU-only contracts: never infer hardware support from these fixture campaigns."""

import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapter.workflow import core
from adapter.workflow.config import validate_environment
from adapter.workflow.evidence.qualify import qualify
from adapter.workflow.executors.commands import command, execute
from adapter.workflow.harnesses.agent import cli_command, handoff, responses
from adapter.workflow.preflight import (
    inspect_environment,
    ssh_command,
)
from adapter.workflow.reporting.reports import release
from adapter.workflow.state import runner


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.campaign = self.root / "metax/fixture/campaign"
        self.result_patch = patch.object(core, "RESULTS", self.root)
        self.result_patch.start()
        self.addCleanup(self.result_patch.stop)
        self.env = {
            "schema_version": 1,
            "name": "fixture-only",
            "execution_enabled": True,
            "nodes": [
                {
                    "id": "local",
                    "ssh": None,
                    "workspace": str(self.root),
                    "runtime": "docker",
                    "device_probe": ["echo", "fixture-device"],
                    "images": ["image@sha256:" + "a" * 64],
                    "models": [str(self.root)],
                }
            ],
            "budgets": {"task_timeout_seconds": 10, "max_attempts": 2, "max_tasks": 10},
        }
        self.envfile = self.root / "environment.json"
        core.atomic(self.envfile, self.env)
        self.source = {"head": "test-fixture", "files_sha256": "b" * 64}
        self.patch1 = patch.object(core, "source_identity", return_value=self.source)
        self.patch1.start()
        self.addCleanup(self.patch1.stop)
        self.patch2 = patch.object(runner, "source_identity", return_value=self.source)
        self.patch2.start()
        self.addCleanup(self.patch2.stop)
        self.envpatch = patch.dict(
            os.environ, {"ADAPTER_LEASE_DIR": str(self.root / "leases")}
        )
        self.envpatch.start()
        self.addCleanup(self.envpatch.stop)

    def init(self, features=None):
        fixture_catalog = copy.deepcopy(core.catalog())
        # Lifecycle tests use fixture observations; real domain predicates have a separate suite.
        for contract in fixture_catalog.values():
            for case in contract["cases"]:
                case.pop("checks", None)
        with patch.object(core, "catalog", return_value=fixture_catalog):
            core.init_campaign(
                self.campaign, "metax", "fixture", self.envfile, features or ["M01-F01"]
            )
        path, frozen, state = core.load_campaign(self.campaign)
        core.atomic(
            path / "preflight.json",
            {
                "identity": frozen["identity"],
                "time": core.now(),
                "environment_sha256": core.fingerprint(self.env),
                "readiness": {
                    r: True for r in frozen["features"]["M01-F01"]["requirements"]
                },
            },
        )
        return path, frozen, state

    def tasks(self, success=True, cleanup=True):
        # Verifier checks a real command's output, then records exact evidence hashes.
        code = """import json, pathlib,hashlib,sys
p=pathlib.Path(sys.argv[1]); e=json.loads((p/'envelope.json').read_text());f=e['feature'];ok=(p/'run-00.log').read_text().strip()=='fixture-output'
artifacts=[]
for role,name in [('command','task.json'),('environment','envelope.json'),('raw_output','run-00.log'),('verifier','verify-00.log')]:
 artifacts.append(dict(id=role,path=name,role=role,sha256=hashlib.sha256((p/name).read_bytes()).hexdigest()))
cases=[dict(id=c['id'],artifacts=[a['id'] for a in artifacts],assertions={k:dict(passed=ok,evidence=['raw_output']) for k in c['required_assertions']}) for c in f['cases']]
(p/'result.json').write_text(json.dumps(dict(identity=e['identity'],feature_id=f['id'],feature_revision=f['revision'],evidence_origin='current_run',artifacts=artifacts,cases=cases)))
"""
        task = {
            "feature_id": "M01-F01",
            "prepare": [],
            "run": [
                {
                    "argv": [
                        sys.executable,
                        "-c",
                        "print('fixture-output')" if success else "raise SystemExit(7)",
                    ]
                }
            ],
            "verify": [{"argv": [sys.executable, "-c", code, "{attempt}"]}],
            "cleanup": [
                {
                    "argv": [
                        sys.executable,
                        "-c",
                        "pass" if cleanup else "raise SystemExit(8)",
                    ]
                }
            ],
            "resources": [],
        }
        core.atomic(
            self.campaign / "tasks.json", {"schema_version": 1, "tasks": [task]}
        )

    def test_catalog_dependencies(self):
        c = core.catalog()
        self.assertEqual(len(c), 45)
        ordered = core.order(c, ["M09-F03"])
        self.assertLess(ordered.index("M09-F02"), ordered.index("M09-F03"))

    def test_cycle_and_unknown(self):
        with self.assertRaises(ValueError):
            core.order({"a": {"dependencies": ["a"]}}, ["a"])
        with self.assertRaises(ValueError):
            core.order({}, ["absent"])

    def test_frozen_identity_cannot_change(self):
        p, f, _ = self.init()
        f["chip"] = "another"
        core.atomic(p / "lock.json", f)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            core.load_campaign(p)

    def test_source_change_stales_campaign(self):
        _p, f, _ = self.init()
        with (
            patch.object(runner, "source_identity", return_value={"head": "different"}),
            self.assertRaisesRegex(ValueError, "Source changed"),
        ):
            runner.check_freshness(f)

    def test_no_commands_blocks_without_fake_pass(self):
        self.init()
        s = runner.run(self.campaign)
        self.assertEqual(s["features"]["M01-F01"]["task"], "blocked")
        self.assertEqual(s["features"]["M01-F01"]["test"], "not_run")

    def test_success_resume_and_release(self):
        self.init()
        self.tasks()
        s = runner.run(self.campaign)
        item = s["features"]["M01-F01"]
        self.assertEqual(item["qualification"], "verified", item)
        s = runner.run(self.campaign)
        self.assertEqual(len(s["features"]["M01-F01"]["attempts"]), 1)
        release(self.campaign, self.campaign / "scope.json")
        with self.assertRaisesRegex(ValueError, "already exists"):
            release(self.campaign, self.campaign / "scope.json")

    def test_failure_is_retained_and_retry_is_new(self):
        self.init()
        self.tasks()
        plan = core.read(self.campaign / "tasks.json")
        marker = self.root / "retry-marker"
        plan["tasks"][0]["run"][0]["argv"] = [
            sys.executable,
            "-c",
            "import pathlib,sys; p=pathlib.Path(sys.argv[1]); existed=p.exists(); p.touch(); print('fixture-output'); sys.exit(0 if existed else 7)",
            str(marker),
        ]
        core.atomic(self.campaign / "tasks.json", plan)
        s = runner.run(self.campaign)
        self.assertEqual(s["features"]["M01-F01"]["task"], "failed")
        s = runner.run(self.campaign)
        self.assertEqual(len(s["features"]["M01-F01"]["attempts"]), 2)
        self.assertTrue(
            (self.campaign / "features/M01-F01/attempt-001/run-00.log").exists()
        )

    def test_cleanup_failure_blocks(self):
        self.init()
        self.tasks(cleanup=False)
        s = runner.run(self.campaign)
        self.assertEqual(s["features"]["M01-F01"]["qualification"], "blocked")

    def test_zero_exit_is_not_certification(self):
        self.init()
        self.tasks()
        task = core.read(self.campaign / "tasks.json")
        task["tasks"][0]["verify"] = [{"argv": [sys.executable, "-c", "pass"]}]
        core.atomic(self.campaign / "tasks.json", task)
        s = runner.run(self.campaign)
        self.assertEqual(s["features"]["M01-F01"]["task"], "succeeded")
        self.assertEqual(s["features"]["M01-F01"]["qualification"], "pending")

    def test_preflight_required_and_missing_assets(self):
        self.env["nodes"][0]["images"] = []
        r = inspect_environment(
            self.env,
            ["ssh", "workspace", "images"],
            runner=lambda *a, **k: {"passed": True, "stdout": "ok"},
        )
        self.assertFalse(r["passed"])
        self.assertFalse(r["readiness"]["images"])

    def test_no_cluster_does_not_block_local_features(self):
        r = inspect_environment(
            self.env,
            ["ssh", "workspace"],
            runner=lambda *a, **k: {"passed": True, "stdout": "ok"},
        )
        self.assertTrue(r["passed"])
        self.assertNotIn("kubernetes", r["readiness"])

    def test_ssh_fail_blocks_dependents(self):
        r = inspect_environment(
            self.env,
            ["ssh", "device"],
            runner=lambda *a, **k: {"passed": False, "stdout": ""},
        )
        self.assertFalse(r["readiness"]["device"])
        self.assertIn("remediation", r["checks"][0])

    def test_ssh_quote_and_batchmode(self):
        c = ssh_command("node", ["echo", "$(no-command)"])
        self.assertIn("BatchMode=yes", c)
        self.assertIn("'$(no-command)'", c[-1])
        with self.assertRaises(ValueError):
            ssh_command("-oProxyCommand=bad", ["true"])

    def test_schema_rejects_fake_result(self):
        _, f, _ = self.init()
        q = qualify({"passed": True}, f, f["features"]["M01-F01"], self.root)
        self.assertEqual(q["qualification"], "pending")

    def test_tampered_artifact_blocks_release(self):
        self.init()
        self.tasks()
        runner.run(self.campaign)
        (self.campaign / "features/M01-F01/attempt-001/run-00.log").write_text(
            "tampered"
        )
        with self.assertRaisesRegex(ValueError, "Evidence changed"):
            release(self.campaign, self.root / "index.json")

    def test_historical_or_wrong_identity_rejected(self):
        self.init()
        self.tasks()
        runner.run(self.campaign)
        _, f, _ = core.load_campaign(self.campaign)
        p = self.campaign / "features/M01-F01/attempt-001"
        result = core.read(p / "result.json")
        result["evidence_origin"] = "historical"
        self.assertEqual(
            qualify(result, f, f["features"]["M01-F01"], p)["qualification"], "pending"
        )
        result["evidence_origin"] = "current_run"
        result["identity"] = "0" * 64
        self.assertEqual(
            qualify(result, f, f["features"]["M01-F01"], p)["qualification"], "pending"
        )

    def test_missing_assertion_rejected(self):
        self.init()
        self.tasks()
        runner.run(self.campaign)
        _, f, _ = core.load_campaign(self.campaign)
        p = self.campaign / "features/M01-F01/attempt-001"
        r = core.read(p / "result.json")
        r["cases"][0]["assertions"].pop("identity")
        self.assertEqual(
            qualify(r, f, f["features"]["M01-F01"], p)["qualification"], "pending"
        )

    def test_running_attempt_needs_reconciliation(self):
        p, f, s = self.init()
        s["features"]["M01-F01"].update(
            task="running",
            attempts=[
                {"pid": None, "path": "features/M01-F01/attempt-001", "resources": []}
            ],
        )
        core.atomic(p / "state.json", s)
        with self.assertRaisesRegex(ValueError, "reconcile"):
            runner.run(p)
        proof = self.root / "proof.json"
        core.atomic(
            proof,
            {
                "identity": f["identity"],
                "feature_id": "M01-F01",
                "owned_resources_absent": True,
                "attempt": "attempt-001",
                "resources": [],
            },
        )
        with self.assertRaisesRegex(ValueError, "evidence"):
            runner.reconcile(p, "M01-F01", proof)
        evidence = self.root / "owned-resources.json"
        core.atomic(evidence, {"remaining": []})
        data = core.read(proof)
        data["artifacts"] = [{"path": evidence.name, "sha256": core.digest(evidence)}]
        core.atomic(proof, data)
        self.assertEqual(
            runner.reconcile(p, "M01-F01", proof)["features"]["M01-F01"]["task"],
            "failed",
        )

    def test_resource_lock(self):
        with (
            core.lock(self.root / "lease"),
            self.assertRaisesRegex(ValueError, "leased"),
            core.lock(self.root / "lease"),
        ):
            pass

    def test_executor_context_and_container(self):
        env = copy.deepcopy(self.env)
        env["kubernetes"] = {"context": "test", "namespace": "owned", "host": None}
        self.assertEqual(
            command({"executor": "kubernetes", "argv": ["get", "pods"]}, env),
            ["kubectl", "--context", "test", "-n", "owned", "get", "pods"],
        )
        self.assertEqual(
            command(
                {
                    "executor": "container",
                    "node": "local",
                    "container": "owned",
                    "argv": ["true"],
                },
                env,
            ),
            ["docker", "exec", "owned", "true"],
        )

    def test_handoff_is_cross_harness_and_not_execution(self):
        self.init()
        for h in ["codex", "claude", "openai"]:
            r = handoff(self.campaign, "M01-F01", h, None)
            self.assertFalse(r["invoked"])
            self.assertTrue(Path(r["envelope"]).exists())
        self.assertEqual(
            core.read(self.campaign / "state.json")["features"]["M01-F01"]["task"],
            "queued",
        )

    def test_cli_schema_args(self):
        c = cli_command("codex", "chosen", Path("schema"), Path("output"))
        self.assertIn("--output-schema", c)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", c)
        c = cli_command("claude", "chosen", Path("schema"), Path("output"))
        self.assertIn("--json-schema", c)

    def test_api_key_required(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"),
        ):
            responses("test", "explicit")

    def test_executor_stdin(self):
        log = self.root / "stdin.log"
        code = execute(
            {"argv": [sys.executable, "-c", "import sys; print(sys.stdin.read())"]},
            self.env,
            self.root,
            log,
            5,
            lambda pid: None,
            stdin_text="fixture-input",
        )
        self.assertEqual(code, 0)
        self.assertEqual(log.read_text().strip(), "fixture-input")

    def test_timeout_reaps_process(self):
        pids = []
        with self.assertRaises(subprocess.TimeoutExpired):
            execute(
                {"argv": [sys.executable, "-c", "import time; time.sleep(30)"]},
                self.env,
                self.root,
                self.root / "timeout.log",
                0.1,
                pids.append,
            )
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)

    def test_duplicate_nodes_rejected(self):
        self.env["nodes"] *= 2
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_environment(self.env)

    def test_release_and_resume_check_dependencies_not_only_requested_feature(self):
        p, frozen, _ = self.init(["M03-F01"])
        report = core.read(p / "preflight.json")
        report["readiness"] = {
            r: True for f in frozen["features"].values() for r in f["requirements"]
        }
        core.atomic(p / "preflight.json", report)
        self.tasks()
        plan = core.read(p / "tasks.json")
        for key in frozen["planned"]:
            if key != "M01-F01":
                task = copy.deepcopy(plan["tasks"][0])
                task["feature_id"] = key
                plan["tasks"].append(task)
        core.atomic(p / "tasks.json", plan)
        state = runner.run(p)
        self.assertEqual(state["features"]["M03-F01"]["qualification"], "verified")
        (p / "features/M01-F01/attempt-001/run-00.log").write_text("damaged dependency")
        with self.assertRaisesRegex(ValueError, "Evidence changed"):
            runner.run(p, only="M03-F01")
        with self.assertRaisesRegex(ValueError, "Evidence changed"):
            release(p, self.root / "scope.json")

    def test_finalization_cannot_be_ready_with_missing_required_work(self):
        from adapter.workflow.reporting import audit

        declared = {
            "proposed_version": "1.0.0",
            "status": "ready",
            "criteria": [
                {
                    "id": "WF-X",
                    "status": "partial",
                    "required": True,
                    "evidence": ["adapter/README.md"],
                    "remaining": ["real work"],
                }
            ],
        }
        with (
            patch.object(audit, "read", return_value=declared),
            self.assertRaisesRegex(ValueError, "Cannot declare ready"),
        ):
            audit.workflow_audit()
        declared["status"] = "not_ready"
        with patch.object(audit, "read", return_value=declared):
            result = audit.workflow_audit()
        self.assertFalse(result["ready"])
        self.assertEqual(result["blockers"], ["WF-X"])

    def test_remote_failure_needs_reconcile_even_when_cleanup_exits_zero(self):
        self.init()
        self.tasks()
        plan = core.read(self.campaign / "tasks.json")
        plan["tasks"][0]["run"][0].update(executor="ssh", node="local")
        core.atomic(self.campaign / "tasks.json", plan)
        with patch.object(
            runner,
            "execute",
            side_effect=lambda spec, *a, **k: (
                255 if spec.get("executor") == "ssh" else 0
            ),
        ):
            state = runner.run(self.campaign)
        self.assertTrue(state["features"]["M01-F01"]["reconciliation_required"])
        with self.assertRaisesRegex(ValueError, "reconcile"):
            runner.run(self.campaign)

    def test_plan_mutation_is_rejected_after_first_attempt(self):
        self.init()
        self.tasks(False)
        runner.run(self.campaign)
        self.tasks()
        with self.assertRaisesRegex(ValueError, "plan changed"):
            runner.run(self.campaign)

    def test_resume_rechecks_verified_evidence(self):
        self.init()
        self.tasks()
        runner.run(self.campaign)
        (self.campaign / "features/M01-F01/attempt-001/run-00.log").write_text(
            "tampered"
        )
        with self.assertRaisesRegex(ValueError, "Evidence changed"):
            runner.run(self.campaign)

    def test_claimed_pass_without_runner_receipt_is_rejected(self):
        self.init()
        self.tasks()
        runner.run(self.campaign)
        _, frozen, _ = core.load_campaign(self.campaign)
        attempt = self.campaign / "features/M01-F01/attempt-001"
        (attempt / "execution.json").unlink()
        result = qualify(
            core.read(attempt / "result.json"),
            frozen,
            frozen["features"]["M01-F01"],
            attempt,
        )
        self.assertEqual(result["qualification"], "pending")
        self.assertTrue(any("Unbound execution" in e for e in result["errors"]))

    def test_result_rewrite_is_rejected_even_with_valid_artifacts(self):
        self.init()
        self.tasks()
        runner.run(self.campaign)
        attempt = self.campaign / "features/M01-F01/attempt-001"
        result = core.read(attempt / "result.json")
        result["unexecuted_claim"] = "added after verifier exited"
        core.atomic(attempt / "result.json", result)
        with self.assertRaisesRegex(ValueError, "Evidence changed"):
            release(self.campaign, self.root / "index.json")

    def test_cleanup_failure_requires_reconcile_before_retry(self):
        self.init()
        self.tasks(cleanup=False)
        state = runner.run(self.campaign)
        self.assertTrue(state["features"]["M01-F01"]["reconciliation_required"])
        with self.assertRaisesRegex(ValueError, "reconcile"):
            runner.run(self.campaign)

    def test_reconcile_does_not_accept_another_attempt(self):
        p, frozen, state = self.init()
        item = state["features"]["M01-F01"]
        item.update(
            task="running",
            attempts=[{"path": "features/M01-F01/attempt-002", "resources": []}],
        )
        core.atomic(p / "state.json", state)
        proof = self.root / "proof.json"
        core.atomic(
            proof,
            {
                "identity": frozen["identity"],
                "feature_id": "M01-F01",
                "attempt": "attempt-001",
                "resources": [],
                "owned_resources_absent": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "matching identity"):
            runner.reconcile(p, "M01-F01", proof)

    def test_wrong_preflight_environment_is_rejected(self):
        p, _, _ = self.init()
        report = core.read(p / "preflight.json")
        report["environment_sha256"] = "wrong"
        core.atomic(p / "preflight.json", report)
        self.tasks()
        with self.assertRaisesRegex(ValueError, "exact campaign and environment"):
            runner.run(p)

    def test_multinode_aliases_are_not_two_machines(self):
        env = copy.deepcopy(self.env)
        env["nodes"].append(dict(env["nodes"][0], id="alias2", ssh="alias2"))
        r = inspect_environment(
            env,
            ["ssh", "multinode"],
            runner=lambda *a, **k: {"passed": True, "stdout": "a" * 32},
        )
        self.assertFalse(r["readiness"]["multinode"])

    def test_model_directory_is_not_immutable_identity(self):
        r = inspect_environment(
            self.env,
            ["models"],
            runner=lambda *a, **k: {"passed": True, "stdout": "ok"},
        )
        self.assertFalse(r["readiness"]["models"])

    def test_model_manifest_rejects_missing_shard_and_modified_bytes(self):
        from adapter.workflow.assets import inspect_model

        model = self.root / "model"
        model.mkdir()
        (model / "config.json").write_text("{}")
        (model / "model-001.safetensors").write_bytes(b"fixture-weights")
        core.atomic(
            model / "model.safetensors.index.json",
            {
                "weight_map": {
                    "a": "model-001.safetensors",
                    "b": "model-002.safetensors",
                }
            },
        )
        manifest = {
            "model_id": "fixture",
            "revision": "r1",
            "files": {p.name: core.digest(p) for p in model.iterdir()},
        }
        core.atomic(model / "manifest.json", manifest)
        declaration = {
            "id": "fixture",
            "revision": "r1",
            "path": str(model),
            "manifest": "manifest.json",
            "manifest_sha256": core.digest(model / "manifest.json"),
        }
        with self.assertRaisesRegex(ValueError, "omits weight"):
            inspect_model(declaration)
        (model / "model-002.safetensors").write_bytes(b"second-shard")
        manifest["files"]["model-002.safetensors"] = core.digest(
            model / "model-002.safetensors"
        )
        core.atomic(model / "manifest.json", manifest)
        declaration["manifest_sha256"] = core.digest(model / "manifest.json")
        self.assertEqual(inspect_model(declaration)["checked_files"], 4)
        (model / "model-001.safetensors").write_bytes(b"tampered-weights")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            inspect_model(declaration)

    def test_remote_paths_are_not_coordinator_paths(self):
        from adapter.workflow.executors.commands import substitute

        env = copy.deepcopy(self.env)
        env["nodes"][0].update(
            ssh="alias", workspace="/remote/repo", container_workspace="/mounted/repo"
        )
        attempt = self.root / "campaign/features/M01-F01/attempt-001"
        spec = substitute(
            {
                "executor": "ssh",
                "node": "local",
                "argv": ["python3", "{repo}/probe.py", "{attempt}"],
            },
            env,
            self.root,
            attempt,
            "identity",
        )
        self.assertEqual(spec["argv"][1], "/remote/repo/probe.py")
        self.assertTrue(
            spec["argv"][2].startswith(
                "/remote/repo/adapter/results/fixture/fixture/identity/"
            )
        )
        self.assertNotIn(str(self.root), str(command(spec, env)))
        spec["executor"] = "container"
        spec["container"] = "owned"
        spec["cwd"] = "/mounted/repo"
        self.assertIn("-w /mounted/repo", command(spec, env)[-1])

    def test_callback_failure_reaps_child(self):
        pids = []

        def failed(pid):
            pids.append(pid)
            raise OSError("disk unavailable")

        with self.assertRaisesRegex(OSError, "disk unavailable"):
            execute(
                {"argv": [sys.executable, "-c", "import time; time.sleep(30)"]},
                self.env,
                self.root,
                self.root / "callback.log",
                1,
                failed,
            )
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)

    def test_canonical_stack_rejects_wrong_platform_and_arbitrary_file(self):
        with self.assertRaisesRegex(ValueError, "Platform ID"):
            core.platform_stack("ascend", "container/platforms/metax.json")
        with self.assertRaisesRegex(ValueError, "canonical"):
            core.platform_stack("metax", str(self.envfile))

    def test_source_fingerprint_includes_build_locks_and_skills(self):
        self.patch1.stop()

        def listing(argv, **kwargs):
            if argv[1] == "rev-parse":
                return "head"
            for path in (
                "Cargo.toml",
                "Cargo.lock",
                "pyproject.toml",
                ".cargo",
                ".agents/skills",
            ):
                self.assertIn(path, argv)
            return b"Cargo.lock\0pyproject.toml\0"

        with patch.object(core.subprocess, "check_output", side_effect=listing):
            before = core.source_identity()
            with patch.object(core, "digest", return_value="modified"):
                after = core.source_identity()
        self.assertNotEqual(before, after)

    def test_invalid_task_cannot_leave_running_state(self):
        self.init()
        self.tasks()
        plan = core.read(self.campaign / "tasks.json")
        plan["tasks"][0]["timeout_seconds"] = -1
        core.atomic(self.campaign / "tasks.json", plan)
        with self.assertRaisesRegex(ValueError, "Invalid task plan"):
            runner.run(self.campaign)
        state = core.read(self.campaign / "state.json")
        self.assertEqual(state["features"]["M01-F01"]["attempts"], [])


if __name__ == "__main__":
    unittest.main()
