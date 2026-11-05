# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create/reconcile only attempt-owned Kubernetes UIDs and boot/start-qualified processes."""

import json
import subprocess
import time
from pathlib import Path

from adapter.workflow.core import (
    atomic,
    digest,
    fingerprint,
    identifier,
    load_campaign,
    now,
    read,
)
from adapter.workflow.executors.commands import command, substitute
from adapter.workflow.preflight import ssh_command

LABEL = "adapter.heyi.io/campaign"
ATTEMPT_LABEL = "adapter.heyi.io/attempt"
PROCESS = r"""
import fcntl,json,os,pathlib,signal,subprocess,sys,time
mode,record_name,identity=sys.argv[1:4];path=pathlib.Path(record_name)
path.parent.mkdir(parents=True,exist_ok=True)
lease=path.with_suffix('.lease').open('a+');fcntl.flock(lease,fcntl.LOCK_EX)
def identity_for(pid):
 try:
  stat=pathlib.Path('/proc/%s/stat'%pid).read_text().rsplit(')',1)[1].split()
  if stat[0]=='Z': return None
  return {'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'start_ticks':stat[19]}
 except FileNotFoundError:return None
if mode=='launch':
 if path.exists():
  record=json.loads(path.read_text())
  if record['identity']!=identity:raise SystemExit('Foreign process record')
  if record.get('argv')!=json.loads(sys.argv[4]):raise SystemExit('Process command changed')
 else:
  path.parent.mkdir(parents=True,exist_ok=True)
  with path.with_suffix('.log').open('ab') as log:
   proc=subprocess.Popen(json.loads(sys.argv[4]),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
  record={'identity':identity,'pid':proc.pid,'process_identity':identity_for(proc.pid),'argv':json.loads(sys.argv[4])}
  temp=path.with_suffix('.partial');temp.write_text(json.dumps(record));temp.replace(path)
else:
 if not path.exists():raise SystemExit('Process ownership record unavailable')
 record=json.loads(path.read_text())
 if record['identity']!=identity:raise SystemExit('Foreign process record')
 live=identity_for(record['pid']); matches=bool(live and live==record['process_identity'])
 if mode=='stop' and matches:
  os.killpg(record['pid'],signal.SIGTERM)
  for _ in range(40):
   if identity_for(record['pid'])!=live:break
   time.sleep(.1)
  if identity_for(record['pid'])==live:os.killpg(record['pid'],signal.SIGKILL)
 record['running']=identity_for(record['pid'])==record['process_identity'] and record['process_identity'] is not None
print(json.dumps(record))
"""


def context(campaign, feature_id, attempt_name):
    path, frozen, state = load_campaign(campaign)
    item = state["features"][feature_id]
    record = next(
        (a for a in item["attempts"] if Path(a["path"]).name == attempt_name), None
    )
    if record is None:
        raise ValueError("Unknown attempt")
    return path, frozen, record, path / record["path"]


def kubectl(env, argv, payload=None):
    args = command({"executor": "kubernetes", "argv": argv}, env)
    result = subprocess.run(
        args, check=False, input=payload, capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        raise ValueError("Kubernetes operation failed: " + result.stderr[-2000:])
    return json.loads(result.stdout) if result.stdout.strip() else None


def registry(attempt):
    file = attempt / "resources.live.json"
    return read(file) if file.exists() else {"resources": []}


def remember(attempt, resource):
    data = registry(attempt)
    prior = next(
        (
            r
            for r in data["resources"]
            if (r["kind"], r.get("node"), r["name"])
            == (resource["kind"], resource.get("node"), resource["name"])
        ),
        None,
    )
    if prior and prior != resource:
        raise ValueError("Resource identity changed; cannot replace ownership record")
    if not prior:
        data["resources"].append(resource)
    atomic(attempt / "resources.live.json", data)
    return resource


def contains(actual, desired):
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            k in actual and contains(actual[k], v) for k, v in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(desired)
            and all(contains(a, b) for a, b in zip(actual, desired))
        )
    return actual == desired


def owner_labels(path, frozen, feature_id, attempt_name):
    return {
        LABEL: fingerprint({"identity": frozen["identity"], "campaign": str(path)})[
            :32
        ],
        ATTEMPT_LABEL: attempt_name,
        "adapter.heyi.io/feature": feature_id,
    }


def launch_job(campaign, feature_id, attempt_name, manifest_path):
    _path, frozen, record, attempt = context(campaign, feature_id, attempt_name)
    manifest = read(manifest_path)
    if manifest.get("kind") != "Job" or manifest.get("apiVersion") != "batch/v1":
        raise ValueError("Owned launcher supports batch/v1 Job")
    metadata = manifest.setdefault("metadata", {})
    namespace = frozen["environment"]["kubernetes"]["namespace"]
    if metadata.get("namespace", namespace) != namespace:
        raise ValueError("Job namespace outside frozen scope")
    name = metadata["name"]
    declared = any(
        r["kind"].lower() == "job" and r["name"] == name for r in record["resources"]
    )
    if not declared:
        raise ValueError("Job is not declared in sealed task resources")
    metadata["namespace"] = namespace
    labels = owner_labels(_path, frozen, feature_id, attempt_name)
    metadata.setdefault("labels", {}).update(labels)
    manifest["spec"]["template"].setdefault("metadata", {}).setdefault(
        "labels", {}
    ).update(labels)
    metadata.setdefault("annotations", {})["adapter.heyi.io/spec-sha256"] = fingerprint(
        manifest["spec"]
    )
    existing = kubectl(
        frozen["environment"], ["get", "job", name, "--ignore-not-found", "-o", "json"]
    )
    if existing:
        actual = existing["metadata"]
        if any(actual.get("labels", {}).get(k) != v for k, v in labels.items()):
            raise ValueError("Foreign Job name collision; never adopt or delete")
        actual_spec = existing.get("spec", {}).get("template", {}).get("spec", {})
        if not contains(actual_spec, manifest["spec"]["template"]["spec"]):
            raise ValueError("Existing owned job has a different workload")
    else:
        if any(
            r["kind"] == "Job" and r["name"] == name
            for r in registry(attempt)["resources"]
        ):
            raise ValueError(
                "Recorded Job is absent; create a new attempt, never recreate it"
            )
        existing = kubectl(
            frozen["environment"],
            ["create", "-f", "-", "-o", "json"],
            json.dumps(manifest),
        )
        actual = existing["metadata"]
    resource = {
        "kind": "Job",
        "name": name,
        "namespace": namespace,
        "uid": actual["uid"],
        "labels": labels,
    }
    return remember(attempt, resource)


def launch_process(campaign, feature_id, attempt_name, node_id, name, argv):
    identifier(name)
    _, frozen, record, attempt = context(campaign, feature_id, attempt_name)
    if not any(
        r["kind"].lower() == "process"
        and r["name"] == name
        and r.get("node") == node_id
        for r in record["resources"]
    ):
        raise ValueError("Process not declared in sealed task")
    nodes = {n["id"]: n for n in frozen["environment"]["nodes"]}
    node = nodes[node_id]
    remote = substitute(
        {
            "executor": "ssh",
            "node": node_id,
            "argv": ["{attempt}/owned-" + name + ".json"],
        },
        frozen["environment"],
        Path(__file__).resolve().parents[3],
        attempt,
        frozen["identity"],
    )["argv"][0]
    prior = next(
        (
            r
            for r in registry(attempt)["resources"]
            if r["kind"] == "process" and r["name"] == name and r.get("node") == node_id
        ),
        None,
    )
    args = ssh_command(
        node.get("ssh"),
        [
            "python3",
            "-c",
            PROCESS,
            "observe" if prior else "launch",
            remote,
            frozen["identity"],
            json.dumps(argv),
        ],
        frozen["environment"].get("ssh_config"),
    )
    output = subprocess.check_output(args, text=True, timeout=30)
    observed = json.loads(output)
    if observed.get("argv") != argv:
        raise ValueError("Recorded process command changed")
    return remember(
        attempt,
        {
            "kind": "process",
            "name": name,
            "node": node_id,
            "remote_record": remote,
            "pid": observed["pid"],
            "process_identity": observed["process_identity"],
        },
    )


def observe(campaign, feature_id, attempt_name, stop=False):
    _path, frozen, record, attempt = context(campaign, feature_id, attempt_name)
    observations = []
    owned = registry(attempt)["resources"]
    # Recover a lost create acknowledgement by the exact attempt owner, never by name alone.
    for declared in record["resources"]:
        if any(
            r["kind"].lower() == declared["kind"].lower()
            and r["name"] == declared["name"]
            and r.get("node") == declared.get("node")
            for r in owned
        ):
            continue
        try:
            if declared["kind"].lower() == "job":
                actual = kubectl(
                    frozen["environment"],
                    [
                        "get",
                        "job",
                        declared["name"],
                        "--ignore-not-found",
                        "-o",
                        "json",
                    ],
                )
                labels = owner_labels(_path, frozen, feature_id, attempt_name)
                if not actual:
                    observations.append({"resource": declared, "absent": True})
                    continue
                meta = actual["metadata"]
                if any(meta.get("labels", {}).get(k) != v for k, v in labels.items()):
                    raise ValueError("Foreign Job; ownership unknown, preserving it")
                resource = {
                    "kind": "Job",
                    "name": declared["name"],
                    "namespace": frozen["environment"]["kubernetes"]["namespace"],
                    "uid": meta["uid"],
                    "labels": labels,
                }
                remember(attempt, resource)
                owned.append(resource)
            elif declared["kind"].lower() == "process":
                node = next(
                    n
                    for n in frozen["environment"]["nodes"]
                    if n["id"] == declared["node"]
                )
                remote = substitute(
                    {
                        "executor": "ssh",
                        "node": node["id"],
                        "argv": [
                            "{attempt}/owned-" + identifier(declared["name"]) + ".json"
                        ],
                    },
                    frozen["environment"],
                    Path(__file__).resolve().parents[3],
                    attempt,
                    frozen["identity"],
                )["argv"][0]
                args = ssh_command(
                    node.get("ssh"),
                    ["python3", "-c", PROCESS, "observe", remote, frozen["identity"]],
                    frozen["environment"].get("ssh_config"),
                )
                actual = json.loads(
                    subprocess.check_output(args, text=True, timeout=15)
                )
                resource = {
                    "kind": "process",
                    "name": declared["name"],
                    "node": node["id"],
                    "remote_record": remote,
                    "pid": actual["pid"],
                    "process_identity": actual["process_identity"],
                }
                remember(attempt, resource)
                owned.append(resource)
            else:
                raise ValueError("Resource requires a site-specific absence probe")
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            observations.append(
                {"resource": declared, "absent": False, "error": str(exc)}
            )
    for resource in owned:
        try:
            if resource["kind"] == "Job":
                actual = kubectl(
                    frozen["environment"],
                    [
                        "get",
                        "job",
                        resource["name"],
                        "--ignore-not-found",
                        "-o",
                        "json",
                    ],
                )
                same = bool(actual and actual["metadata"]["uid"] == resource["uid"])
                if same and any(
                    actual["metadata"].get("labels", {}).get(k) != v
                    for k, v in resource["labels"].items()
                ):
                    raise ValueError("Owner labels changed")
                if stop and same:
                    # UID precondition prevents deleting a new object that reused the name.
                    path_api = (
                        "/apis/batch/v1/namespaces/"
                        + resource["namespace"]
                        + "/jobs/"
                        + resource["name"]
                    )
                    kubectl(
                        frozen["environment"],
                        ["delete", "--raw", path_api, "-f", "-"],
                        json.dumps(
                            {
                                "apiVersion": "v1",
                                "kind": "DeleteOptions",
                                "propagationPolicy": "Foreground",
                                "preconditions": {"uid": resource["uid"]},
                            }
                        ),
                    )
                    for _ in range(45):
                        actual = kubectl(
                            frozen["environment"],
                            [
                                "get",
                                "job",
                                resource["name"],
                                "--ignore-not-found",
                                "-o",
                                "json",
                            ],
                        )
                        if not actual or actual["metadata"]["uid"] != resource["uid"]:
                            break
                        time.sleep(1)
                    same = bool(actual and actual["metadata"]["uid"] == resource["uid"])
                observations.append(
                    {
                        "resource": resource,
                        "absent": not same,
                        "actual_uid": actual["metadata"]["uid"] if actual else None,
                    }
                )
            elif resource["kind"] == "process":
                node = next(
                    n
                    for n in frozen["environment"]["nodes"]
                    if n["id"] == resource["node"]
                )
                args = ssh_command(
                    node.get("ssh"),
                    [
                        "python3",
                        "-c",
                        PROCESS,
                        "stop" if stop else "observe",
                        resource["remote_record"],
                        frozen["identity"],
                    ],
                    frozen["environment"].get("ssh_config"),
                )
                actual = json.loads(
                    subprocess.check_output(args, text=True, timeout=15)
                )
                if (
                    actual["pid"] != resource["pid"]
                    or actual["process_identity"] != resource["process_identity"]
                ):
                    raise ValueError("Remote process record changed")
                observations.append(
                    {
                        "resource": resource,
                        "absent": not actual["running"],
                        "actual": actual,
                    }
                )
            else:
                raise ValueError("Unsupported owned resource kind")
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            observations.append(
                {"resource": resource, "absent": False, "error": str(exc)}
            )
    return {
        "identity": frozen["identity"],
        "feature_id": feature_id,
        "attempt": attempt_name,
        "resources": record["resources"],
        "owned_resources_absent": all(x["absent"] for x in observations),
        "observations": observations,
        "time": now(),
    }


def recover(campaign, feature_id, stop=False):
    path, _frozen, state = load_campaign(campaign)
    record = state["features"][feature_id]["attempts"][-1]
    attempt_name = Path(record["path"]).name
    proof = observe(campaign, feature_id, attempt_name, stop)
    target = path / record["path"] / "recovery-probes" / now().replace(":", "-")
    atomic(target / "resources.json", proof)
    proof["artifacts"] = [
        {"path": "resources.json", "sha256": digest(target / "resources.json")}
    ]
    atomic(target / "proof.json", proof)
    if not proof["owned_resources_absent"]:
        raise ValueError(
            "Owned resources remain or could not be observed; see recovery-probes"
        )
    from adapter.workflow.state.runner import reconcile

    return reconcile(campaign, feature_id, target / "proof.json")
