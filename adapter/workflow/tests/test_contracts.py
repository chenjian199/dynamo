# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Negative contracts for scope, measurements, identities, isolation and delivery."""

import copy
import io
import tarfile
from unittest.mock import patch

import pytest

from adapter.workflow.bindings import case_environment
from adapter.workflow.core import atomic, catalog, digest, fingerprint
from adapter.workflow.delivery import validate_smoke
from adapter.workflow.evidence.verify import evaluate, evaluate_case
from adapter.workflow.executors.resources import contains, launch_job, owner_labels
from adapter.workflow.executors.transfers import safe_extract
from adapter.workflow.harnesses.isolation import allowed, apply
from adapter.workflow.identity import freshness_map, signature
from adapter.workflow.scope import resolve
from adapter.workflow.state.compile import compile_plan


def small():
    return {
        k: {"id": k, "dependencies": deps, "cases": [{"id": k + "-C01"}]}
        for k, deps in [("a", []), ("b", ["a"]), ("c", [])]
    }


def test_optional_deferred_and_anyof():
    f = small()
    f["b"]["dependencies"] = []
    f["b"]["dependencies_any_of"] = [["a", "c"]]
    scope, ordered, selected = resolve(
        f,
        None,
        {
            "features": {
                "b": {"disposition": "required"},
                "c": {
                    "disposition": "deferred",
                    "reason": "No cluster",
                    "accepted_by": "operator",
                    "reference": "decision-1",
                },
            },
            "dependency_choices": {"b": ["a"]},
        },
    )
    assert ordered == ["a", "b"] and scope["features"]["c"]["disposition"] == "deferred"
    assert selected["b"]["dependencies"] == ["a"]


@pytest.mark.parametrize(
    "declaration",
    [
        {"features": {"a": {"disposition": "deferred"}}},
        {
            "features": {
                "b": {"disposition": "required"},
                "a": {"disposition": "out_of_scope", "reason": "excluded"},
            }
        },
        {
            "features": {
                "a": {
                    "disposition": "required",
                    "cases": {"bad": {"disposition": "required"}},
                }
            }
        },
    ],
)
def test_invalid_scope_rejected(declaration):
    with pytest.raises(ValueError):
        resolve(small(), None, declaration)


def test_anyof_must_choose_and_cycle():
    f = small()
    f["a"]["dependencies_any_of"] = [["b", "c"]]
    with pytest.raises(ValueError, match="choose"):
        resolve(f, ["a"])
    with pytest.raises(ValueError, match="cycle"):
        resolve(f, ["a"], {"dependency_choices": {"a": ["b"]}})


def test_precise_catalog_cases():
    features = catalog()
    assert len(features) == 45 and sum(len(f["cases"]) for f in features.values()) == 68
    for f in features.values():
        for c in f["cases"]:
            assert set(c["checks"]) == set(c["required_assertions"])
            assert c["recipe"]["entrypoints"]
            assert c["negative_cases"] and c["native_control"]


def test_scoped_staleness_and_downstream():
    features = small()
    for k, f in features.items():
        f.update(
            implementation_paths=["components/" + k],
            contract_path="adapter/modules/" + k + "/feature.yaml",
        )
    source = {"files": {"components/a/a.py": "1", "components/c/c.py": "1"}}
    frozen = {
        "features": features,
        "source": source,
        "stack_profile": "container/platforms/test.json",
    }
    frozen["feature_inputs"] = {
        k: signature(f, source, frozen["stack_profile"], fingerprint)
        for k, f in features.items()
    }
    current = copy.deepcopy(source)
    current["files"]["components/a/a.py"] = "2"
    states = freshness_map(frozen, current, fingerprint)
    assert (
        states["a"]["direct"]
        and states["b"]["freshness"] == "stale"
        and states["c"]["freshness"] == "valid"
    )


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf"), "1"])
def test_positive_rejects_fake_numbers(value, tmp_path):
    assert not evaluate({"op": "positive", "path": "/x"}, {"x": value}, tmp_path, {})


def test_parity_checks_bytes_and_nonempty(tmp_path):
    (tmp_path / "a").write_bytes(b"ab")
    (tmp_path / "b").write_bytes(b"ac")
    check = {"op": "file_parity", "path": "/a", "other": "/b"}
    assert not evaluate(check, {"a": "a", "b": "b"}, tmp_path, {})
    (tmp_path / "b").write_bytes(b"ab")
    assert evaluate(check, {"a": "a", "b": "b"}, tmp_path, {})
    (tmp_path / "a").write_bytes(b"")
    (tmp_path / "b").write_bytes(b"")
    assert not evaluate(check, {"a": "a", "b": "b"}, tmp_path, {})
    assert not evaluate(
        {"op": "equal_fields", "path": "/a", "other": "/b"},
        {"a": True, "b": True},
        tmp_path,
        {},
    )


def test_binding_no_topology_whitelist():
    feature = {"requirements": ["images", "models"]}
    case = {"id": "C"}
    image = "image@sha256:" + "a" * 64
    env = {
        "nodes": [
            {
                "id": "n",
                "images": [image],
                "models": [
                    {"id": "qwen", "path": "/models/qwen", "manifest_sha256": "b" * 64}
                ],
            }
        ],
        "case_bindings": {
            "C": {
                "nodes": ["n"],
                "image": image,
                "models": ["qwen"],
                "topology": {"tp": 8, "pp": 4, "dp": 3},
            }
        },
    }
    resolved, binding = case_environment(env, feature, case)
    assert (
        binding["topology"]["tp"] == 8
        and resolved["nodes"][0]["models"][0]["id"] == "qwen"
    )
    bad = copy.deepcopy(env)
    bad["case_bindings"]["C"]["models"] = ["absent"]
    with pytest.raises(ValueError):
        case_environment(bad, feature, case)


def test_missing_measurements_not_pass(tmp_path):
    result, errors = evaluate_case(
        {"id": "C", "checks": {}}, tmp_path, {"environment": {}, "feature": {}}
    )
    assert not result and errors


def test_archive_traversal_rejected(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as output:
        entry = tarfile.TarInfo("../escape")
        entry.size = 1
        output.addfile(entry, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape").exists()


def test_patch_protected_and_concurrent(tmp_path):
    assert not allowed("container/platforms/cuda.json", ["container"])
    assert not allowed("adapter/workflow/evidence/verify.py", ["adapter"])
    assert allowed("components/x.py", ["components"])
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    target = tmp_path / "target"
    for root in (workspace, repo):
        (root / "components").mkdir(parents=True)
        (root / "components/x.py").write_text("old")
    inventory = {"components/x.py": digest(repo / "components/x.py")}
    (workspace / "components/x.py").write_text("new")
    (repo / "components/x.py").write_text("concurrent")
    with (
        patch("adapter.workflow.harnesses.isolation.ROOT", repo),
        pytest.raises(ValueError, match="Concurrent"),
    ):
        apply(workspace, inventory, ["components"], target)
    assert (repo / "components/x.py").read_text() == "concurrent"


def test_owned_job_adopts_without_duplicate_create(tmp_path):
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "owned"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": "test", "image": "x"}],
                    "restartPolicy": "Never",
                }
            }
        },
    }
    atomic(tmp_path / "job.json", manifest)
    frozen = {
        "identity": "a" * 64,
        "environment": {"kubernetes": {"namespace": "test"}},
    }
    labels = owner_labels(tmp_path, frozen, "F", "attempt-001")
    actual = copy.deepcopy(manifest)
    actual["metadata"].update(uid="uid-1", labels=labels)
    actual["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = (
        "IfNotPresent"
    )
    with (
        patch(
            "adapter.workflow.executors.resources.context",
            return_value=(
                tmp_path,
                frozen,
                {"resources": [{"kind": "Job", "name": "owned"}]},
                tmp_path,
            ),
        ),
        patch(
            "adapter.workflow.executors.resources.kubectl", return_value=actual
        ) as call,
    ):
        assert (
            launch_job(tmp_path, "F", "attempt-001", tmp_path / "job.json")["uid"]
            == "uid-1"
        )
        assert call.call_count == 1
        actual["metadata"]["labels"] = {}
        with pytest.raises(ValueError, match="Foreign"):
            launch_job(tmp_path, "F", "attempt-001", tmp_path / "job.json")
    assert contains({"a": 1, "default": 2}, {"a": 1})


def test_import_smoke_rejects_tag_or_version_drift(tmp_path):
    frozen = {
        "identity": "i",
        "stack_sha256": "s",
        "stack": {"versions": {"vllm": "0.28.0"}},
    }
    report = {
        "identity": "i",
        "stack_sha256": "s",
        "versions": {"vllm": "0.28.0"},
        "requests": [
            {"http_status": 200, "output_tokens": 1, "actual": [4], "expected": [4]}
        ],
        "images": [
            {"role": r, "expected_id": "sha256:a", "observed_id": "sha256:a"}
            for r in ("runtime", "dev", "tools", "operator")
        ],
    }
    atomic(tmp_path / "smoke.json", report)
    assert validate_smoke(tmp_path, {"smoke_report": "smoke.json"}, frozen)
    report["images"][0]["observed_id"] = "sha256:b"
    atomic(tmp_path / "smoke.json", report)
    with pytest.raises(ValueError, match="identity"):
        validate_smoke(tmp_path, {"smoke_report": "smoke.json"}, frozen)


def test_recipe_compile_rejects_unbound_commands():
    feature = catalog()["M01-F01"]
    case = feature["cases"][0]
    frozen = {
        "planned": [feature["id"]],
        "features": {feature["id"]: feature},
        "platform": "metax",
        "identity": "i",
        "environment": {
            "nodes": [{"id": "local", "workspace": "."}],
            "budgets": {"max_tasks": 2},
            "case_bindings": {
                case["id"]: {
                    "nodes": ["local"],
                    "topology": {},
                    "commands": {
                        "run": [{"argv": ["true"]}],
                        "observe": [{"argv": ["true"]}],
                        "cleanup": [{"argv": ["true"]}],
                    },
                }
            },
        },
    }
    plan, missing = compile_plan(frozen)
    assert not plan["tasks"] and missing[0]["required"]


def test_recipe_compile_invokes_precise_entry_and_fixed_verifier():
    feature = copy.deepcopy(catalog()["M01-F01"])
    feature["requirements"] = []
    case = feature["cases"][0]
    recipe = {
        "run": [{"argv": ["python3", "{repo}/" + case["recipe"]["entrypoints"][0]]}],
        "observe": [{"argv": ["true"]}],
        "cleanup": [{"argv": ["true"]}],
    }
    frozen = {
        "planned": [feature["id"]],
        "features": {feature["id"]: feature},
        "platform": "metax",
        "identity": "i",
        "environment": {
            "nodes": [{"id": "local", "workspace": "."}],
            "budgets": {"max_tasks": 2},
            "case_bindings": {
                case["id"]: {"nodes": ["local"], "topology": {}, "commands": recipe}
            },
        },
    }
    plan, missing = compile_plan(frozen)
    assert (
        not missing
        and "adapter.workflow.evidence.verify" in plan["tasks"][0]["verify"][0]["argv"]
    )


def test_different_campaigns_never_share_resource_owner(tmp_path):
    frozen = {"identity": "a" * 64}
    assert owner_labels(tmp_path / "one", frozen, "F", "attempt-001") != owner_labels(
        tmp_path / "two", frozen, "F", "attempt-001"
    )
    assert owner_labels(tmp_path, frozen, "F", "attempt-001") != owner_labels(
        tmp_path, frozen, "G", "attempt-001"
    )


def test_live_registry_does_not_delete_foreign_replacement(tmp_path):
    from adapter.workflow.executors.resources import observe

    resource = {
        "kind": "Job",
        "name": "owned",
        "namespace": "test",
        "uid": "old",
        "labels": {},
    }
    atomic(tmp_path / "resources.live.json", {"resources": [resource]})
    frozen = {"identity": "i", "environment": {}}
    with (
        patch(
            "adapter.workflow.executors.resources.context",
            return_value=(
                tmp_path,
                frozen,
                {"resources": [{"kind": "Job", "name": "owned"}]},
                tmp_path,
            ),
        ),
        patch(
            "adapter.workflow.executors.resources.kubectl",
            return_value={"metadata": {"uid": "foreign-new"}},
        ) as call,
    ):
        result = observe(tmp_path, "F", "attempt-001", stop=True)
        assert result["owned_resources_absent"] and call.call_count == 1


def test_inheritance_rechecks_original_bytes():
    # Use a lifecycle fixture, never claim real chip certification.
    from test_workflow import WorkflowTest

    from adapter.workflow import core
    from adapter.workflow.evidence.inheritance import evidence_for, inherit
    from adapter.workflow.state import runner

    case = WorkflowTest()
    case.setUp()
    try:
        source, _, _ = case.init()
        case.tasks()
        runner.run(source)
        case.campaign = source.with_name("inherited")
        target, frozen, state = case.init()
        inherit(target, source, "M01-F01", "unit-review", "Same fixture inputs")
        _, _, state = core.load_campaign(target)
        attempt, gate = evidence_for(target, frozen, state, "M01-F01")
        assert gate["qualification"] == "verified" and attempt.is_relative_to(source)
        (attempt / "run-00.log").write_text("tampered")
        with pytest.raises(ValueError):
            evidence_for(target, frozen, state, "M01-F01")
    finally:
        case.doCleanups()


def test_remote_process_launch_is_idempotent_under_concurrency(tmp_path):
    import json
    import subprocess
    import sys

    from adapter.workflow.executors.resources import PROCESS

    record = str(tmp_path / "owned.json")
    argv = [
        sys.executable,
        "-c",
        PROCESS,
        "launch",
        record,
        "fixture-identity",
        json.dumps(["sleep", "20"]),
    ]
    children = [
        subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for _ in range(4)
    ]
    try:
        outputs = []
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, stderr
            outputs.append(json.loads(stdout))
        assert len({r["pid"] for r in outputs}) == 1
    finally:
        result = subprocess.run(
            [sys.executable, "-c", PROCESS, "stop", record, "fixture-identity"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    assert json.loads(result.stdout)["running"] is False


def test_pod_preflight_selects_bound_container_and_digest():
    import json

    from adapter.workflow.preflight import probe_runtime

    image = "registry/x@sha256:" + "a" * 64
    env = {
        "nodes": [{"id": "n"}],
        "kubernetes": {"context": "test", "namespace": "test", "host": None},
    }
    binding = {"image": image, "pods": {"n": "pod"}, "pod_containers": {"n": "engine"}}
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        if "get" in argv:
            return {
                "passed": True,
                "stdout": json.dumps(
                    {
                        "status": {
                            "containerStatuses": [
                                {
                                    "name": "engine",
                                    "imageID": "containerd://sha256:" + "a" * 64,
                                },
                                {"name": "sidecar", "imageID": "sha256:" + "b" * 64},
                            ]
                        }
                    }
                ),
            }
        return {"passed": True, "stdout": "{}"}

    result = probe_runtime(env, binding, {"versions": {}}, execute)
    assert result["status"] == "matched" and "-c" in calls[-1] and "engine" in calls[-1]
    binding.pop("pod_containers")
    assert (
        probe_runtime(env, binding, {"versions": {}}, execute)["status"] == "mismatch"
    )


def test_optional_dependency_does_not_become_mandatory():
    scope, _, _ = resolve(
        small(),
        None,
        {
            "features": {
                "b": {"disposition": "optional"},
                "c": {"disposition": "required"},
            }
        },
    )
    assert scope["features"]["a"]["disposition"] == "optional"
    assert scope["features"]["c"]["disposition"] == "required"


def test_disappeared_job_is_not_recreated_in_same_attempt(tmp_path):
    from adapter.workflow.executors.resources import remember

    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "gone"},
        "spec": {"template": {"spec": {"containers": [{"name": "x", "image": "x"}]}}},
    }
    atomic(tmp_path / "job.json", manifest)
    frozen = {"identity": "i", "environment": {"kubernetes": {"namespace": "test"}}}
    remember(tmp_path, {"kind": "Job", "name": "gone", "uid": "old", "labels": {}})
    with (
        patch(
            "adapter.workflow.executors.resources.context",
            return_value=(
                tmp_path,
                frozen,
                {"resources": [{"kind": "Job", "name": "gone"}]},
                tmp_path,
            ),
        ),
        patch(
            "adapter.workflow.executors.resources.kubectl", return_value=None
        ) as call,
    ):
        with pytest.raises(ValueError, match="never recreate"):
            launch_job(tmp_path, "F", "attempt-001", tmp_path / "job.json")
        assert call.call_count == 1
