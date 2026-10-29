# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Opt-in test observer, loaded only via vLLM's --worker-extension-cls.

Counts successful native API lifetime operations without replacing their results.
This module is never imported by production Dynamo. Per-process files are atomic;
counts supplement (not replace) engine occupancy and process memory measurements.
"""

import functools
import json
import os
import threading
import time
from pathlib import Path

_lock = threading.RLock()
_state = {"pid": os.getpid(), "calls": {}, "active": {}, "peak": {}}
_sets = {}
_root = Path(os.environ["M05_OBSERVER_DIR"])
_root.mkdir(parents=True, exist_ok=True)


def _save():
    _state["updated_monotonic"] = time.monotonic()
    target = _root / f"native-{os.getpid()}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(_state, sort_keys=True) + "\n")
    tmp.replace(target)


def _record(name, category=None, key=None, add=True):
    with _lock:
        _state["calls"][name] = _state["calls"].get(name, 0) + 1
        if category:
            items = _sets.setdefault(category, set())
            if add:
                items.add(key)
            else:
                items.discard(key)
            _state["active"][category] = len(items)
            _state["peak"][category] = max(len(items), _state["peak"].get(category, 0))
        _save()


def _wrap(cls, name, category=None, add=True, result_key=False):
    original = getattr(cls, name)

    @functools.wraps(original)
    def wrapped(self, *args, **kwargs):
        value = args[0] if category and not result_key else None
        result = original(self, *args, **kwargs)
        if category and result_key:
            value = result
        key = (
            (id(self), value if isinstance(value, (str, int)) else id(value))
            if category
            else None
        )
        _record(name, category, key, add)
        return result

    setattr(cls, name, wrapped)


def _install():
    platform = os.environ["DYN_PLATFORM"]
    connector = os.environ["M05_OBSERVER_CONNECTOR"]
    if connector == "nixl":
        from nixl._api import nixl_agent

        for name in ("initialize_xfer", "make_prepped_xfer"):
            _wrap(nixl_agent, name, "transfers", result_key=True)
        _wrap(nixl_agent, "release_xfer_handle", "transfers", add=False)
        _wrap(nixl_agent, "prep_xfer_dlist", "prepared", result_key=True)
        _wrap(nixl_agent, "release_dlist_handle", "prepared", add=False)
        _wrap(nixl_agent, "add_remote_agent", "peers", result_key=True)
        _wrap(nixl_agent, "remove_remote_agent", "peers", add=False)
        # Registration descriptors are opaque native containers; count calls,
        # not Python repr identities, which need not survive deregistration.
        for name in ("register_memory", "deregister_memory", "transfer"):
            _wrap(nixl_agent, name)
    else:
        if platform == "ascend":
            from dynamo.common.platform.ascend_mooncake import (
                prepare_mooncake_libraries,
            )

            prepare_mooncake_libraries()
        from mooncake.engine import TransferEngine

        for name in ("register_memory", "unregister_memory"):
            _wrap(
                TransferEngine,
                name,
                "registered_addresses",
                add=name == "register_memory",
            )
        for name in ("batch_transfer_sync_read", "batch_transfer_sync_write"):
            original = getattr(TransferEngine, name)

            def make_call(method, method_name):
                @functools.wraps(method)
                def call(self, *args, **kwargs):
                    token = (id(self), threading.get_ident())
                    _record(method_name + ":start", "sync_calls", token)
                    try:
                        result = method(self, *args, **kwargs)
                        _record(method_name + (":failed" if result < 0 else ":done"))
                        return result
                    finally:
                        _record(method_name + ":return", "sync_calls", token, False)

                return call

            setattr(TransferEngine, name, make_call(original, name))
    _state.update(platform=platform, connector=connector)
    with _lock:
        _save()


_install()


class TransferObserver:
    """Empty extension: import installs the test-only native API observer."""


# Explicit fault-only instrumentation; the normal/soak path above does not
# change transport submission semantics. This exposes a native pending state
# then holds the caller so the harness can kill exactly that owned process.
def _fault_claim():
    directory = os.environ.get("M05_INFLIGHT_DIR")
    if not directory:
        return None
    root = Path(directory)
    try:
        (root / "arm").rename(root / f"claimed-{os.getpid()}")
    except FileNotFoundError:
        return None
    return root


def _fault_event(root, phase, **extra):
    path = root / f"{phase}.json"
    tmp = root / f"{phase}-{os.getpid()}.tmp"
    tmp.write_text(
        json.dumps(
            dict(pid=os.getpid(), phase=phase, monotonic=time.monotonic(), **extra)
        )
    )
    tmp.replace(path)


def _fault_before(root):
    _fault_event(root, "before")
    deadline = time.monotonic() + 30
    while not (root / "submit").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Fault harness did not release submission barrier")
        time.sleep(0.005)


def _fault_pending(root, state):
    _fault_event(root, "pending", native_state=state)
    # Never throw an exception into a connector with device work still active:
    # an absent harness fails by terminating this test process, not by reporting
    # fake completion or returning buffers to the scheduler.
    time.sleep(45)
    os._exit(70)


def _install_fault():
    if not os.environ.get("M05_INFLIGHT_DIR"):
        return
    if os.environ["M05_OBSERVER_CONNECTOR"] == "nixl":
        from nixl._api import nixl_agent

        original = nixl_agent.transfer

        def transfer(self, *args, **kwargs):
            root = _fault_claim()
            if root is None:
                return original(self, *args, **kwargs)
            _fault_before(root)
            result = original(self, *args, **kwargs)
            state = self.check_xfer_state(args[0])
            if state != "PROC":
                _fault_event(root, "missed", native_state=state)
                return result
            _fault_pending(root, state)

        nixl_agent.transfer = transfer
    else:
        from mooncake.engine import TransferEngine

        name = (
            "batch_transfer_sync_write"
            if os.environ["DYN_PLATFORM"] == "metax"
            else "batch_transfer_sync_read"
        )
        original = getattr(TransferEngine, name)
        async_name = name.replace("_sync_", "_async_")

        def sync(self, *args, **kwargs):
            root = _fault_claim()
            if root is None:
                return original(self, *args, **kwargs)
            _fault_before(root)
            batch = getattr(self, async_name)(*args, **kwargs)
            if not batch:
                raise RuntimeError("Native fault-test submission failed")
            state = self.transfer_check_status(batch)
            if state != 0:
                _fault_event(root, "missed", native_state=state)
                return 0 if state == 1 else -1
            _fault_pending(root, state)

        setattr(TransferEngine, name, sync)


_install_fault()
