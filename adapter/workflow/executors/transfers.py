# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Owned SSH source staging and byte-preserving evidence collection."""

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from adapter.workflow.core import ROOT, atomic, digest, fingerprint, load_campaign, now
from adapter.workflow.executors.commands import substitute
from adapter.workflow.preflight import ssh_command

REMOTE_STAGE = r"""
import hashlib,json,pathlib,sys,tarfile,tempfile
root=pathlib.Path(sys.argv[1]); identity=sys.argv[2]; expected=sys.argv[3]
marker=root/'.adapter-workflow-owner.json'
if root.exists() and any(root.iterdir()):
 if not marker.is_file() or json.loads(marker.read_text()).get('identity')!=identity:
  raise SystemExit('Refusing to overwrite a non-owned workspace')
root.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryFile() as stream:
 h=hashlib.sha256()
 while True:
  block=sys.stdin.buffer.read(1024*1024)
  if not block: break
  stream.write(block);h.update(block)
 if h.hexdigest()!=expected: raise SystemExit('Source archive checksum mismatch')
 stream.seek(0)
 with tarfile.open(fileobj=stream,mode='r:*') as archive:
  members=archive.getmembers()
  for m in members:
   target=(root/m.name).resolve()
   if not target.is_relative_to(root.resolve()) or not m.isfile(): raise SystemExit('Unsafe source member')
  marker.write_text(json.dumps({'identity':identity,'archive_sha256':expected,'state':'staging'}))
  for m in members:
   target=root/m.name;target.parent.mkdir(parents=True,exist_ok=True)
   with archive.extractfile(m) as src, target.open('wb') as out:
    while True:
     b=src.read(1024*1024)
     if not b: break
     out.write(b)
   target.chmod(m.mode & 0o777)
 marker.write_text(json.dumps({'identity':identity,'archive_sha256':expected,'state':'complete'}))
print(json.dumps({'identity':identity,'archive_sha256':expected,'state':'complete'}))
"""
REMOTE_COLLECT = r"""
import pathlib,sys,tarfile
root=pathlib.Path(sys.argv[1]).resolve()
if not root.is_dir(): raise SystemExit('Remote attempt absent')
files=sorted(root.rglob('*'))
if any(p.is_symlink() for p in files): raise SystemExit('Evidence symlink rejected')
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
 for p in files:
  if p.is_file() and p.name not in ('.lock',): archive.add(p,arcname=str(p.relative_to(root)),recursive=False)
"""


def safe_extract(archive_path, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path) as archive:
        for item in archive.getmembers():
            target = (destination / item.name).resolve()
            if not target.is_relative_to(destination) or not item.isfile():
                raise ValueError("Unsafe evidence archive member")
            if target.exists():
                raise ValueError("Evidence collection cannot overwrite a previous file")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def node_for(frozen, node_id):
    nodes = {n["id"]: n for n in frozen["environment"]["nodes"]}
    if node_id not in nodes:
        raise ValueError("Unknown transfer node")
    return nodes[node_id]


def stage(campaign, node_id):
    path, frozen, _ = load_campaign(campaign)
    node = node_for(frozen, node_id)
    if not node.get("ssh"):
        raise ValueError("Source staging needs an explicit remote SSH node")
    directory = path / "transfers" / node_id / ("stage-" + now().replace(":", "-"))
    directory.mkdir(parents=True)
    archive = directory / "source.tar.gz"
    files = frozen["source"].get("files", {})
    if not files:
        raise ValueError("Source staging requires a complete frozen source inventory")
    with tarfile.open(archive, "w:gz") as out:
        for name, checksum in sorted(files.items()):
            file = (ROOT / name).resolve()
            if (
                not file.is_relative_to(ROOT)
                or not file.is_file()
                or digest(file) != checksum
            ):
                raise ValueError("Frozen source changed before staging: " + name)
            out.add(file, arcname=name, recursive=False)
    checksum = digest(archive)
    argv = ssh_command(
        node["ssh"],
        [
            "python3",
            "-c",
            REMOTE_STAGE,
            node["workspace"],
            frozen["identity"],
            checksum,
        ],
        frozen["environment"].get("ssh_config"),
    )
    with archive.open("rb") as stream:
        process = subprocess.run(
            argv,
            check=False,
            stdin=stream,
            capture_output=True,
            timeout=frozen["environment"]["budgets"]["task_timeout_seconds"],
        )
    (directory / "stdout.log").write_bytes(process.stdout)
    (directory / "stderr.log").write_bytes(process.stderr)
    if process.returncode:
        raise ValueError("Remote staging failed; see transfer logs")
    receipt = json.loads(process.stdout)
    if (
        receipt.get("identity") != frozen["identity"]
        or receipt.get("archive_sha256") != checksum
    ):
        raise ValueError("Remote staging receipt mismatch")
    atomic(
        directory / "receipt.json",
        dict(receipt, node=node_id, source_inventory_sha256=fingerprint(files)),
    )
    return {"receipt": str(directory / "receipt.json"), "workspace": node["workspace"]}


def collect(campaign, node_id, feature_id, attempt_name, promote_cases=False):
    path, frozen, state = load_campaign(campaign)
    node = node_for(frozen, node_id)
    item = state["features"][feature_id]
    allowed = {Path(a["path"]).name for a in item["attempts"]}
    if attempt_name not in allowed:
        raise ValueError("Collect requires a recorded attempt")
    attempt = path / "features" / feature_id / attempt_name
    spec = substitute(
        {"executor": "ssh", "node": node_id, "argv": ["{attempt}"]},
        frozen["environment"],
        ROOT,
        attempt,
        frozen["identity"],
    )
    target = attempt / "remote" / node_id
    if target.exists():
        raise ValueError("Collection already exists; preserve prior evidence")
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.with_suffix(".tar.partial")
    argv = ssh_command(
        node.get("ssh"),
        ["python3", "-c", REMOTE_COLLECT, spec["argv"][0]],
        frozen["environment"].get("ssh_config"),
    )
    with archive.open("wb") as output:
        process = subprocess.run(
            argv,
            check=False,
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=frozen["environment"]["budgets"]["task_timeout_seconds"],
        )
    if process.returncode:
        archive.with_suffix(".error.log").write_bytes(process.stderr)
        raise ValueError("Remote collection failed")
    safe_extract(archive, target)
    receipt = {
        "identity": frozen["identity"],
        "node": node_id,
        "remote": spec["argv"][0],
        "archive_sha256": digest(archive),
        "files": {
            str(p.relative_to(target)): digest(p)
            for p in target.rglob("*")
            if p.is_file()
        },
    }
    atomic(target / "collection.json", receipt)
    if promote_cases:
        for case in frozen["features"][feature_id]["cases"]:
            source = target / "cases" / case["id"]
            destination = attempt / "cases" / case["id"]
            if source.is_dir():
                if destination.exists():
                    raise ValueError(
                        "Case already collected; use explicit aggregation for multi-node observations"
                    )
                shutil.copytree(source, destination)
    return receipt


def stage_inputs(campaign, node_id, feature_id, attempt_name):
    """Copy only recorded attempt inputs after source staging; retain canonical relative paths."""
    path, frozen, state = load_campaign(campaign)
    node = node_for(frozen, node_id)
    record = next(
        (
            a
            for a in state["features"][feature_id]["attempts"]
            if Path(a["path"]).name == attempt_name
        ),
        None,
    )
    if record is None:
        raise ValueError("Inputs require a recorded attempt")
    attempt = path / record["path"]
    directory = attempt / "transfers" / node_id
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "inputs.tar.gz"
    if archive.exists():
        raise ValueError("Input transfer already recorded")
    with tarfile.open(archive, "w:gz") as out:
        for name in ("task.json", "envelope.json", "preflight.json", "resources.json"):
            file = attempt / name
            out.add(file, arcname=str(file.relative_to(ROOT)), recursive=False)
    checksum = digest(archive)
    argv = ssh_command(
        node["ssh"],
        [
            "python3",
            "-c",
            REMOTE_STAGE,
            node["workspace"],
            frozen["identity"],
            checksum,
        ],
        frozen["environment"].get("ssh_config"),
    )
    with archive.open("rb") as stream:
        result = subprocess.run(
            argv,
            stdin=stream,
            capture_output=True,
            timeout=frozen["environment"]["budgets"]["task_timeout_seconds"],
            check=False,
        )
    (directory / "inputs.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError("Input staging failed; inspect inputs.log")
    receipt = json.loads(result.stdout)
    if receipt.get("archive_sha256") != checksum:
        raise ValueError("Input staging hash mismatch")
    atomic(directory / "inputs-receipt.json", receipt)
    return receipt
