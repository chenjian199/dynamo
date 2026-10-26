# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded LOCAL embedding lifetime with process-death-safe POSIX leases.

An expired, unclaimed file can be removed by any live worker's collector.
Readers hold flock leases; process exit releases them in the kernel. If every
worker exits, collection resumes when the next worker starts. This is for a
shared local filesystem, not an NFS/distributed lease protocol.
"""

import errno
import fcntl
import logging
import math
import os
import stat
import tempfile
import threading
import time
from functools import lru_cache
from itertools import chain
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalMediaStore:
    def __init__(self, root: str, ttl: float = 300):
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("LOCAL media TTL must be finite and positive")
        self.root = Path(root).resolve()
        self.ttl = ttl

    def acquire(self, path: str):
        """Pin a published file until loading/consumption finishes."""
        candidate = Path(path)
        if candidate.parent.resolve() != self.root or not (
            candidate.name.startswith("encoder_cache.")
            and candidate.name.endswith(".safetensors")
        ):
            raise ValueError("LOCAL embedding path is outside the transfer store")
        fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stream = os.fdopen(fd, "rb")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("LOCAL embedding is not a regular file")
            current = candidate.stat(follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise FileNotFoundError("LOCAL embedding was reclaimed")
            if time.time() - opened.st_mtime >= self.ttl:
                raise TimeoutError("LOCAL embedding lease expired before acquisition")
            return stream
        except BaseException:
            stream.close()
            raise

    def collect(self) -> int:
        """Remove expired files only while holding their exclusive lease."""
        removed = 0
        for path in chain(
            self.root.glob("encoder_cache.*.safetensors"),
            self.root.glob("encoder_cache.*.partial"),
        ):
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    continue
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb"):
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    opened = os.fstat(fd)
                    if time.time() - opened.st_mtime < self.ttl:
                        continue
                    current = path.stat(follow_symlinks=False)
                    if (opened.st_dev, opened.st_ino) == (
                        current.st_dev,
                        current.st_ino,
                    ):
                        path.unlink()
                        removed += 1
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ELOOP):
                    raise
                continue  # Another collector removed it, or a symlink replaced it.
        return removed

    def start_collector(self):
        def collect_forever():
            while True:
                try:
                    self.collect()
                except OSError:
                    logger.exception("LOCAL media collection failed in %s", self.root)
                time.sleep(min(30, self.ttl / 2))

        threading.Thread(
            target=collect_forever, name="local-media-collector", daemon=True
        ).start()


@lru_cache(maxsize=None)
def _process_store(pid: int, root: str, ttl: float) -> LocalMediaStore:
    store = LocalMediaStore(root, ttl)
    store.start_collector()
    return store


def local_media_store() -> LocalMediaStore:
    return _process_store(
        os.getpid(),
        tempfile.gettempdir(),
        float(os.environ.get("DYN_LOCAL_MEDIA_TTL_SECONDS", "300")),
    )
