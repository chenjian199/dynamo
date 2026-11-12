# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real disposable Git histories and negative evidence gates; no GPU or external services."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapter.workflow.core import atomic, digest, fingerprint, read
from adapter.workflow.delivery import PHASES
from adapter.workflow.upgrade import gates, gitops, images, session
from adapter.workflow.upgrade.analysis import closure
from adapter.workflow.upgrade.cli import main
from adapter.workflow.upgrade.replan import replan_scope


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.g("init", "-b", "main")
        self.g("config", "user.name", "Fixture")
        self.g("config", "user.email", "fixture@example.invalid")
        self.write(".gitignore", ".worktrees/\n")
        self.write("engine.py", "old\n")
        self.write("other.py", "old\n")
        atomic(
            self.repo / "container/platforms/cuda.json", {"versions": {"vllm": "0.1.0"}}
        )
        self.commit("base")
        self.g("tag", "v1.0.0")
        self.g("checkout", "-b", "upstream-fixture")
        self.write("other.py", "new upstream\n")
        self.commit("upstream")
        self.g("tag", "v2.0.0")
        self.target = gitops.revision(self.repo, "HEAD")
        self.g("checkout", "-b", "heyi", "v1.0.0")
        self.write("engine.py", "heyi port\n")
        self.commit("port")
        self.head = gitops.revision(self.repo, "HEAD")
        self.features = {
            "M01-F01": {
                "dependencies": [],
                "implementation_paths": ["engine.py"],
                "contract_path": "features/m01.yaml",
                "revision": 1,
                "cases": [{"id": "M01-F01-C01"}],
            },
            "M03-F01": {
                "dependencies": ["M01-F01"],
                "implementation_paths": ["other.py"],
                "contract_path": "features/m03.yaml",
                "revision": 1,
                "cases": [{"id": "M03-F01-C01"}],
            },
            "M10-F01": {
                "dependencies": ["M03-F01"],
                "implementation_paths": ["delivery"],
                "contract_path": "features/m10.yaml",
                "revision": 1,
                "cases": [{"id": "M10-F01-C01"}],
            },
        }
        self.plan = {
            "schema_version": 1,
            "id": "fixture",
            "upstream_url": "https://github.com/ai-dynamo/dynamo.git",
            "refs": {"base": "v1.0.0", "heyi": "HEAD", "target": "v2.0.0"},
            "target_commit": self.target,
            "platforms": {
                "cuda": {
                    "architecture": "amd64",
                    "vllm": "0.2.0",
                    "repository": "example.invalid/cuda",
                    "image": "example.invalid/cuda:2",
                    "python": "python3",
                    "sources": ["https://example.invalid/release"],
                }
            },
            "mandatory_features": ["M01-F01"],
            "checks": [["python3", "-c", 'print("fixture check")']],
            "check_timeout_seconds": 10,
            "comparison_limits": {
                "min_throughput_ratio": 0.9,
                "max_latency_ratio": 1.1,
            },
        }
        self.planfile = self.root / "plan.json"
        self.patcher = patch.object(session, "RESULTS", self.root / "results")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.receipt = {
            "requested_image": "example.invalid/cuda:2",
            "source": "local-image-inspect-and-metadata",
            "architecture": "amd64",
            "repo_digest": "example.invalid/cuda@sha256:" + "a" * 64,
            "image_id": "sha256:" + "b" * 64,
            "versions": {"vllm": "0.2.0"},
        }

    def g(self, *args, repo=None):
        return gitops.git(repo or self.repo, *args)

    def write(self, name, content):
        p = self.repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def commit(self, message, repo=None):
        self.g("add", "-A", repo=repo)
        self.g("commit", "--no-gpg-sign", "-s", "-m", message, repo=repo)

    def init(self):
        atomic(self.planfile, self.plan)
        self.s = session.initialize(self.repo, self.planfile, "test", self.features)
        return self.s

    def bind(self):
        file = self.root / "receipt.json"
        atomic(file, self.receipt)
        session.bind_image(self.s, "cuda", file)

    def merged(self):
        self.init()
        self.bind()
        result = session.prepare(self.s)
        self.tree = Path(result["path"])
        self.commit("reviewed fixture merge", self.tree)
        self.candidate = gitops.revision(self.tree, "HEAD")

    def comparisons(self):
        runs = {}
        conditions = {
            "workload_sha256": "a" * 64,
            "weights_sha256": "b" * 64,
            "hardware": {"device": "fixture", "count": 2},
            "parallelism": {"tp": 2},
            "load": {"concurrency": 1},
            "sampling": {"temperature": 0},
            "metric_definition": "output tokens/sec and p95 ms",
        }
        for role, sha in [
            ("old_heyi", self.head),
            ("new_heyi", self.candidate),
            ("native", None),
        ]:
            raw = self.s / (role + ".txt")
            raw.write_text(
                "synthetic fixture measurement, never hardware certification\n"
            )
            runs[role] = {
                "source_commit": sha,
                "vllm": "0.1.0" if role == "old_heyi" else "0.2.0",
                "conditions": copy.deepcopy(conditions),
                "correct": True,
                "failed_requests": 0,
                "metrics": {"throughput": 10, "ttft_p95_ms": 5, "tpot_p95_ms": 2},
                "artifacts": [{"path": raw.name, "sha256": digest(raw)}],
            }
        self.comparison = self.s / "comparison.json"
        atomic(self.comparison, {"runs": runs})
        return runs

    def test_freeze_both_deltas_and_dependency_closure(self):
        self.init()
        _, frozen, _, analysis = session.load(self.s)
        self.assertEqual(frozen["commits"]["target"], self.target)
        self.assertEqual(analysis["required_features"], sorted(self.features))
        item = next(i for i in analysis["ledger"] if i["path"] == "engine.py")
        self.assertFalse(item["upstream_overlap"])
        self.assertEqual(item["disposition"], "pending")

    def test_dirty_main_rejected(self):
        self.write("engine.py", "uncommitted\n")
        with self.assertRaisesRegex(ValueError, "Commit workflow"):
            self.init()

    def deferred(self):
        return {
            "disposition": "deferred",
            "reason": "Fixture lacks delivery environment",
            "accepted_by": "fixture operator",
            "reference": "fixture explicit scope",
        }

    def test_replan_scope_preserves_parent_and_resets_runtime_gates(self):
        self.merged()
        item = read(self.s / "analysis.json")["ledger"][0]
        session.review(self.s, item["id"], "retain", "fixture source review", "test")
        session.check(self.s)
        self.comparisons()
        gates.compare(self.s, "cuda", self.comparison)
        before = {p.name: p.read_bytes() for p in self.s.iterdir() if p.is_file()}
        self.plan["id"] = "scoped"
        self.plan["deferred_features"] = {"M10-F01": self.deferred()}
        atomic(self.planfile, self.plan)
        new = replan_scope(self.s, self.planfile, "scoped", "test", "explicit deferral")
        _, frozen, state, analysis = session.load(new)
        self.assertEqual(frozen["commits"]["heyi"], self.head)
        self.assertEqual(analysis["required_features"], ["M01-F01", "M03-F01"])
        self.assertEqual(state["reviews"][item["id"]]["commit"], self.candidate)
        for gate in ["checks", "campaigns", "comparisons", "deliveries"]:
            self.assertFalse(state[gate])
        for name, data in before.items():
            self.assertEqual((self.s / name).read_bytes(), data)
        self.assertEqual(digest(new / "parent-lock.json"), digest(self.s / "lock.json"))
        self.assertEqual(
            digest(new / "parent-state.json"), digest(self.s / "state.json")
        )
        handoff = session.handoff(new, "cuda", self.root / "site.yaml", "fixture")
        proposed = read(
            new / "handoffs" / "cuda" / self.candidate / "scope.proposed.json"
        )
        self.assertEqual(proposed["features"]["M10-F01"], self.deferred())
        self.assertEqual(handoff["candidate"], self.candidate)
        self.assertIn(
            "cuda:fresh_M01_M10_campaign_pending", session.status(new)["blockers"]
        )
        with self.assertRaisesRegex(ValueError, "Session exists"):
            replan_scope(self.s, self.planfile, "scoped", "test", "retry")
        (new / "parent-state.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "parent snapshot changed"):
            session.load(new)

    def test_scope_replan_cannot_remove_mandatory_or_dependency(self):
        self.merged()
        for feature, message in [
            ("M01-F01", "Mandatory"),
            ("M03-F01", "deferred dependency"),
        ]:
            self.plan["deferred_features"] = {feature: self.deferred()}
            atomic(self.planfile, self.plan)
            with self.assertRaisesRegex(ValueError, message):
                replan_scope(
                    self.s, self.planfile, feature, "test", "fixture rejection"
                )
            self.assertFalse((self.root / "results" / feature).exists())
        self.plan["deferred_features"] = {"M10-F01": self.deferred()}
        self.plan["comparison_limits"]["min_throughput_ratio"] = 0.1
        atomic(self.planfile, self.plan)
        with self.assertRaisesRegex(ValueError, "cannot change"):
            replan_scope(self.s, self.planfile, "weakened", "test", "fixture rejection")

    def test_deferrals_require_acceptance_and_cannot_hide_skipped_cases(self):
        self.plan["deferred_features"] = {"M10-F01": self.deferred()}
        for field in ["reason", "accepted_by", "reference"]:
            bad = copy.deepcopy(self.plan)
            bad["deferred_features"]["M10-F01"][field] = " "
            atomic(self.planfile, bad)
            self.assertEqual(main(["validate-plan", "--plan", str(self.planfile)]), 2)
        self.merged()
        campaign = self.tree / "adapter/results/cuda/fixture/fake"
        frozen = {
            "platform": "cuda",
            "source": {"head": self.candidate},
            "required": ["M01-F01", "M03-F01"],
            "stack": {
                "versions": {"vllm": "0.2.0"},
                "base_image": self.receipt["repo_digest"],
            },
            "features": copy.deepcopy(self.features),
            "scope": {"features": {"M10-F01": self.deferred()}},
        }
        frozen["scope"]["features"]["M10-F01"]["reference"] = "unapproved replacement"
        atomic(campaign / "lock.json", frozen)
        with self.assertRaisesRegex(ValueError, "exact accepted deferral"):
            session.attach_campaign(self.s, "cuda", campaign)
        frozen["scope"]["features"]["M10-F01"] = self.deferred()
        frozen["features"]["M03-F01"]["case_scope"] = {
            "M03-F01-C01": {"disposition": "optional"}
        }
        atomic(campaign / "lock.json", frozen)
        with self.assertRaisesRegex(ValueError, "weaken required cases"):
            session.attach_campaign(self.s, "cuda", campaign)

    def test_wrong_official_commit_rejected(self):
        self.plan["target_commit"] = "a" * 40
        with self.assertRaisesRegex(ValueError, "official release commit"):
            self.init()

    def test_plan_rejects_rc_target(self):
        self.plan["refs"]["target"] = "v2.0.0rc1"
        atomic(self.planfile, self.plan)
        self.assertEqual(main(["validate-plan", "--plan", str(self.planfile)]), 2)

    def test_lock_and_analysis_tamper_rejected(self):
        self.init()
        analysis = read(self.s / "analysis.json")
        analysis["required_features"] = []
        atomic(self.s / "analysis.json", analysis)
        with self.assertRaisesRegex(ValueError, "Impact analysis changed"):
            session.load(self.s)

    def test_requires_real_images_and_never_modifies_heyi(self):
        self.init()
        with self.assertRaisesRegex(ValueError, "image receipts"):
            session.prepare(self.s)
        self.bind()
        result = session.prepare(self.s)
        self.assertEqual(result["status"], "merged_uncommitted")
        self.assertEqual(gitops.revision(self.repo, "HEAD"), self.head)
        self.assertTrue(gitops.clean(self.repo))
        with self.assertRaisesRegex(ValueError, "uncommitted"):
            session.check(self.s)
        again = session.prepare(self.s)
        self.assertEqual(again["path"], result["path"])

    def test_conflict_is_preserved_not_auto_resolved(self):
        self.g("checkout", "upstream-fixture")
        self.write("engine.py", "upstream same line\n")
        self.commit("conflicting upstream")
        self.target = gitops.revision(self.repo, "HEAD")
        self.g("tag", "-f", "v2.0.0")  # disposable fixture only
        self.g("checkout", "heyi")
        self.plan["target_commit"] = self.target
        self.init()
        self.bind()
        result = session.prepare(self.s)
        self.assertEqual(result["status"], "conflicts")
        self.assertIn("engine.py", result["conflicts"])
        self.assertEqual(gitops.revision(self.repo, "HEAD"), self.head)
        self.assertIn("<<<<<<<", (Path(result["path"]) / "engine.py").read_text())

    def test_existing_unowned_directory_not_reused(self):
        self.init()
        self.bind()
        (self.repo / ".worktrees/test").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "not owned"):
            session.prepare(self.s)

    def test_wrong_version_and_architecture_receipts(self):
        for field, value in [("architecture", "arm64"), ("image_id", "tag-only")]:
            receipt = dict(self.receipt, **{field: value})
            with self.assertRaises(ValueError):
                images.validate_receipt(self.plan["platforms"]["cuda"], receipt)
        receipt = dict(self.receipt, versions={"vllm": "0.1.0"})
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            images.validate_receipt(self.plan["platforms"]["cuda"], receipt)

    def test_bound_image_cannot_change(self):
        self.init()
        self.bind()
        self.receipt["image_id"] = "sha256:" + "c" * 64
        with self.assertRaisesRegex(ValueError, "already frozen"):
            self.bind()

    def test_probe_pins_id_and_never_pulls_or_attaches_device(self):
        info = [
            {
                "Id": "sha256:" + "b" * 64,
                "Architecture": "amd64",
                "RepoDigests": [self.receipt["repo_digest"]],
            }
        ]
        with patch.object(
            images,
            "invoke",
            side_effect=[
                json.dumps(info),
                json.dumps({"versions": {"vllm": "0.2.0"}, "python": "3.12.0"}),
            ],
        ) as run:
            result = images.probe(
                self.plan["platforms"]["cuda"], "example.invalid/cuda:2"
            )
        command = run.call_args_list[1].args[0]
        self.assertIn("--pull=never", command)
        self.assertIn("--network=none", command)
        self.assertNotIn("--gpus", command)
        self.assertIn(info[0]["Id"], command)
        self.assertEqual(result["repo_digest"], self.receipt["repo_digest"])

    def test_check_and_reviews_expire_on_new_commit(self):
        self.merged()
        analysis = read(self.s / "analysis.json")
        for item in analysis["ledger"]:
            session.review(
                self.s, item["id"], "retain", "fixture reviewed target API", "test"
            )
        session.review(
            self.s, "upstream", "reviewed", "fixture capability audit", "test"
        )
        self.assertEqual(session.check(self.s)["status"], "passed")
        before = session.status(self.s)
        self.assertNotIn("candidate_checks_pending", before["blockers"])
        self.assertFalse(
            any(x.startswith("migration_reviews_pending") for x in before["blockers"])
        )
        (self.tree / "engine.py").write_text("a later fix\n")
        self.commit("new candidate", self.tree)
        after = session.status(self.s)
        self.assertIn("candidate_checks_pending", after["blockers"])
        self.assertTrue(
            any(x.startswith("migration_reviews_pending") for x in after["blockers"])
        )

    def test_failed_check_retained(self):
        self.plan["checks"] = [["python3", "-c", "raise SystemExit(1)"]]
        self.merged()
        self.assertEqual(session.check(self.s)["status"], "failed")
        self.assertEqual(session.check(self.s)["status"], "failed")
        self.assertEqual(len(read(self.s / "state.json")["checks"]), 2)

    def test_handoff_only_no_campaign_deployed(self):
        self.merged()
        handoff = session.handoff(self.s, "cuda", self.root / "site.yaml", "fixture")
        self.assertEqual(handoff["candidate"], self.candidate)
        self.assertFalse((self.tree / handoff["campaign"]).exists())
        self.assertEqual(handoff["commands"][1][3], "doctor")

    def test_changed_measurements_block_and_native_is_required(self):
        self.merged()
        runs = self.comparisons()
        self.assertEqual(
            gates.compare(self.s, "cuda", self.comparison)["status"], "passed"
        )
        (self.s / "native.txt").write_text("changed")
        self.assertIn(
            "cuda:comparison_evidence_changed", session.status(self.s)["blockers"]
        )
        with self.assertRaisesRegex(ValueError, "artifact"):
            gates.compare(self.s, "cuda", self.comparison)
        del runs["native"]
        atomic(self.comparison, {"runs": runs})
        with self.assertRaisesRegex(ValueError, "native engine"):
            gates.compare(self.s, "cuda", self.comparison)

    def test_unmatched_conditions_and_nonfinite_metrics_fail(self):
        self.merged()
        runs = self.comparisons()
        runs["native"]["conditions"]["hardware"]["count"] = 64
        atomic(self.comparison, {"runs": runs})
        with self.assertRaisesRegex(ValueError, "Unmatched"):
            gates.compare(self.s, "cuda", self.comparison)
        runs = self.comparisons()
        runs["new_heyi"]["metrics"]["throughput"] = float("nan")
        atomic(self.comparison, {"runs": runs})
        with self.assertRaisesRegex(ValueError, "finite"):
            gates.compare(self.s, "cuda", self.comparison)

    def test_performance_regression_does_not_pass(self):
        self.merged()
        runs = self.comparisons()
        runs["new_heyi"]["metrics"]["throughput"] = 1
        atomic(self.comparison, {"runs": runs})
        result = gates.compare(self.s, "cuda", self.comparison)
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "cuda:paired_comparison_pending", session.status(self.s)["blockers"]
        )

    def test_promotion_without_hardware_evidence_fails_closed(self):
        self.merged()
        with self.assertRaisesRegex(ValueError, "Missing qualified campaign"):
            gates.promotion(self.s, self.root / "rollback.json")
        self.assertFalse((self.s / "promotion.json").exists())

    def test_case_coverage_cannot_be_weakened(self):
        self.merged()
        campaign = self.tree / "adapter/results/cuda/fixture/fake"
        frozen = {
            "platform": "cuda",
            "source": {"head": self.candidate},
            "required": list(self.features),
            "stack": {
                "versions": {"vllm": "0.2.0"},
                "base_image": self.receipt["repo_digest"],
            },
            "features": copy.deepcopy(self.features),
        }
        frozen["features"]["M01-F01"]["cases"] = []
        atomic(campaign / "lock.json", frozen)
        # Test only structural gate before invoking the real feature evidence auditor.
        with (
            patch.object(session.gitops, "clean", return_value=True),
            self.assertRaisesRegex(ValueError, "case contract"),
        ):
            session.attach_campaign(self.s, "cuda", campaign)

    def test_any_of_dependencies_are_conservatively_included(self):
        features = copy.deepcopy(self.features)
        features["M10-F01"]["dependencies"] = []
        features["M10-F01"]["dependencies_any_of"] = [["M01-F01", "M03-F01"]]
        self.assertEqual(closure(features, ["M01-F01"]), sorted(features))

    def test_maintenance_release_need_not_be_ancestor_of_target(self):
        self.g("checkout", "-b", "maintenance", "v1.0.0")
        self.write("hotfix.py", "maintenance backport\n")
        self.commit("maintenance release")
        self.g("tag", "v1.0.1")
        self.g("checkout", "heyi")
        self.g("merge", "--no-edit", "--no-gpg-sign", "v1.0.1")
        self.plan["refs"]["base"] = "v1.0.1"
        self.init()
        frozen = read(self.s / "lock.json")
        self.assertNotEqual(frozen["merge_base"], frozen["commits"]["base"])

    def test_result_artifacts_do_not_dirty_source(self):
        self.merged()
        file = self.tree / "adapter/results/cuda/fixture/run/output.json"
        atomic(file, {"fixture": True})
        self.assertTrue(gitops.clean(self.tree))
        (self.tree / "engine.py").write_text("real implementation change\n")
        self.assertFalse(gitops.clean(self.tree))

    def test_changed_assertion_with_same_case_id_rejected(self):
        self.merged()
        campaign = self.tree / "adapter/results/cuda/fixture/fake"
        frozen = {
            "platform": "cuda",
            "source": {"head": self.candidate},
            "required": list(self.features),
            "features": copy.deepcopy(self.features),
            "stack": {
                "versions": {"vllm": "0.2.0"},
                "base_image": self.receipt["repo_digest"],
            },
        }
        frozen["features"]["M01-F01"]["cases"][0]["required_assertions"] = []
        atomic(campaign / "lock.json", frozen)
        with self.assertRaisesRegex(ValueError, "case contract"):
            session.attach_campaign(self.s, "cuda", campaign)

    def test_sealed_delivery_and_tampered_archive(self):
        campaign = self.root / "delivery-campaign"
        root = campaign / "delivery"
        frozen = {
            "identity": "fixture",
            "stack_sha256": "stack",
            "stack": {"versions": {"vllm": "fixture"}},
        }
        atomic(campaign / "lock.json", frozen)
        atomic(
            root / "smoke.json",
            {
                "identity": "fixture",
                "stack_sha256": "stack",
                "versions": {"vllm": "fixture"},
                "images": [
                    {"role": r, "expected_id": r, "observed_id": r}
                    for r in ["runtime", "dev", "tools", "operator"]
                ],
                "requests": [
                    {
                        "http_status": 200,
                        "output_tokens": 1,
                        "actual": "fixture",
                        "expected": "fixture",
                    }
                ],
            },
        )
        (root / "bundle.tar").write_bytes(b"fixture offline bytes")
        plan = {"archive": "bundle.tar", "smoke_report": "smoke.json"}
        phases = {
            p: {
                "status": "completed",
                "artifacts": {"smoke.json": digest(root / "smoke.json")},
            }
            for p in PHASES
        }
        file = root / "delivery-state.json"
        atomic(
            file,
            {
                "identity": "fixture",
                "status": "artifacts_sealed",
                "plan": plan,
                "plan_sha256": fingerprint(plan),
                "lock_sha256": digest(campaign / "lock.json"),
                "archive_sha256": digest(root / "bundle.tar"),
                "phases": phases,
            },
        )
        self.assertEqual(
            gates.verify_delivery(file, campaign)["archive"], str(root / "bundle.tar")
        )
        (root / "bundle.tar").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Archive checksum"):
            gates.verify_delivery(file, campaign)

    def test_recovered_core_is_not_gitignored(self):
        from adapter.workflow.core import ROOT

        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "adapter/workflow/core.py"],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
