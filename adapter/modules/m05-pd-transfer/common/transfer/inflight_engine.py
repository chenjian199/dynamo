# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Own-process crash at an observed native pending transfer, before first token."""

import json
import signal
import time
from concurrent.futures import ThreadPoolExecutor

import psutil


def run(args, workers, complete, idle):
    root = args.output.resolve() / "inflight"
    root.mkdir(exist_ok=True)
    submitter = (
        "prefill" if args.platform == "metax" and args.connector != "nixl" else "decode"
    )
    peer_role = "decode" if submitter == "prefill" else "prefill"
    peer_processes = psutil.Process(workers[peer_role].proc.pid).children(
        recursive=True
    )
    peer_engines = [p for p in peer_processes if "EngineCore" in p.name()]
    if len(peer_engines) != 1:
        raise RuntimeError("In-flight test requires one TP1 peer engine")
    peer = peer_engines[0]
    stopped = False
    report = dict(status="failed", submitter=submitter, peer=peer_role)

    def event(phase):
        deadline = time.monotonic() + 30
        while not (root / f"{phase}.json").exists():
            if (root / "missed.json").exists():
                raise RuntimeError(
                    "Native transfer finished before pending observation"
                )
            if time.monotonic() > deadline:
                raise TimeoutError(f"Missing native {phase} marker")
            time.sleep(0.005)
        return json.loads((root / f"{phase}.json").read_text())

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            (root / "arm").touch(exist_ok=False)
            request = pool.submit(complete, 0)
            before = event("before")
            owned = {
                p.pid
                for p in psutil.Process(workers[submitter].proc.pid).children(
                    recursive=True
                )
            }
            if before["pid"] not in owned:
                raise RuntimeError("Refusing to signal an unowned native process")
            peer.send_signal(signal.SIGSTOP)
            stopped = True
            (root / "submit").touch()
            pending = event("pending")
            if pending["pid"] != before["pid"]:
                raise RuntimeError("Native caller identity changed")
            psutil.Process(pending["pid"]).send_signal(signal.SIGKILL)
            peer.send_signal(signal.SIGCONT)
            stopped = False
            start = time.monotonic()
            try:
                response = request.result(timeout=90)
            except Exception as exc:
                # A client deadline alone does not prove backend failure.
                import requests

                if isinstance(exc, (TimeoutError, requests.exceptions.Timeout)):
                    raise RuntimeError(
                        "Request hung after native process exit"
                    ) from exc
                report["request_error"] = repr(exc)
            else:
                choices = response.get("choices", [])
                if not choices or any(
                    c.get("finish_reason") != "error" for c in choices
                ):
                    raise RuntimeError(
                        f"Native crash returned a false success: {response}"
                    )
                report["response"] = response
            report.update(
                before=before, pending=pending, error_seconds=time.monotonic() - start
            )
        # Surviving peer may hold source blocks until the connector's lease.
        try:
            report["survivor_idle"] = idle(peer_role)
            report["peer_outcome"] = "drained"
        except Exception:
            # CUDA UCX may also terminate its passive peer on endpoint failure.
            # This is fail-stop recovery, not in-process resource reclamation.
            if peer.is_running() and peer.status() != psutil.STATUS_ZOMBIE:
                raise
            report["peer_outcome"] = "engine_exited_requires_restart"
        report["status"] = "passed"
    finally:
        if stopped:
            peer.send_signal(signal.SIGCONT)
        (args.output / "inflight-engine.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    return report
