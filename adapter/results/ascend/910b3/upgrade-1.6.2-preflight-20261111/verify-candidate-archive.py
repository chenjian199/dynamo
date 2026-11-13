# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independently stream-check the final candidate tar against its file inventory."""
import argparse
import hashlib
import json
import tarfile
import time
from pathlib import Path, PurePosixPath

parser = argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
record = {'started': time.time(), 'passed': False, 'release_qualified': False}


def digest(stream):
    value = hashlib.sha256()
    for data in iter(lambda: stream.read(4 * 1024 * 1024), b''):
        value.update(data)
    return value.hexdigest()


try:
    with args.archive.open('rb') as stream:
        record['sha256'] = digest(stream)
    with tarfile.open(args.archive, 'r:') as archive:
        members = archive.getmembers()
        assert len({m.name for m in members}) == len(members), 'Duplicate tar members'
        assert all(m.isfile() or m.isdir() for m in members), 'Unexpected link or special entry'
        assert all(not PurePosixPath(m.name).is_absolute() and '..' not in PurePosixPath(m.name).parts
                   for m in members), 'Unsafe archive path'
        roots = {PurePosixPath(m.name).parts[0] for m in members}
        assert len(roots) == 1
        prefix = next(iter(roots)) + '/'
        manifest = archive.extractfile(prefix + 'SHA256SUMS').read().decode()
        entries = [line.split('  ', 1) for line in manifest.splitlines()]
        expected = {name: checksum for checksum, name in entries}
        assert len(entries) == len(expected), 'Duplicate checksum entries'
        actual = {m.name.removeprefix(prefix): m for m in members
                  if m.isfile() and m.name != prefix + 'SHA256SUMS'}
        assert set(actual) == set(expected), 'File inventory mismatch'
        for name, member in actual.items():
            with archive.extractfile(member) as stream:
                assert digest(stream) == expected[name], name
        candidate = json.load(archive.extractfile(prefix + 'candidate.json'))
        assert candidate['release_qualified'] is False
        assert candidate['status'] == 'archived_candidate_not_release_qualified'
        record.update(passed=True, files=len(actual), archive=str(args.archive),
                      bytes=args.archive.stat().st_size,
                      scope='Archive byte integrity and candidate label; not an empty-cluster installation test')
except Exception as error:
    record['error'] = repr(error)
record['finished'] = time.time()
args.output.write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record), flush=True)
raise SystemExit(0 if record['passed'] else 1)
