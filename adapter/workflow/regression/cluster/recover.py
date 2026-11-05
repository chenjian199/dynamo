#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""After a successful steady run, cancel streams and replace only campaign Pods."""

import argparse
import copy
import json
import shlex
import subprocess
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ssh-config", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--namespace", default="dynamo-regression-1028")
    p.add_argument("--cancel-manifest", type=Path, required=True)
    p.add_argument("--pair-manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "passed": False,
        "faults": [],
        "qualification": "post-load Pod replacement; no claim of transparent in-flight migration",
    }

    def remote(command, data=None):
        proc = subprocess.run(
            [
                "ssh",
                "-F",
                args.ssh_config,
                args.host,
                shlex.join(["kubectl", "-n", args.namespace, *command]),
            ],
            input=data,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return proc.stdout

    def wait_job(name, seconds):
        deadline = time.monotonic() + seconds
        while True:
            job = json.loads(remote(["get", "job", name, "-o", "json"]))
            if job["status"].get("failed"):
                raise RuntimeError(f"Job failed: {name}")
            if job["status"].get("succeeded"):
                log = remote(["logs", f"job/{name}"])
                (args.output / f"{name}.log").write_text(log)
                rows = [
                    json.loads(line.split("=", 1)[1])
                    for line in log.splitlines()
                    if line.startswith("REGRESSION_RESULT=")
                ]
                if not rows or not rows[-1].get("passed"):
                    raise RuntimeError(f"No passing result from {name}")
                return rows[-1]
            if time.monotonic() > deadline:
                raise TimeoutError(name)
            time.sleep(5)

    try:
        steady = wait_job("pd-load-30m", 2700)
        report["steady"] = {k: steady[k] for k in ["passed", "completed", "elapsed"]}
        # Let the no-restart resource observer finish its post-load idle samples.
        time.sleep(210)
        cancellation = json.loads(args.cancel_manifest.read_text())
        remote(["apply", "-f", "-"], json.dumps(cancellation))
        report["cancellation"] = wait_job(cancellation["metadata"]["name"], 900)
        template = json.loads(args.pair_manifest.read_text())
        if template["kind"] == "List":
            template = next(
                x for x in template["items"] if x["metadata"]["name"] == "pd-pairs-2p3d"
            )
        for index, app in enumerate(["pd-decode-0", "pd-prefill-0", "pd-decode-1"], 1):
            pods = json.loads(
                remote(
                    [
                        "get",
                        "pods",
                        "-l",
                        f"app={app},campaign={args.namespace}",
                        "-o",
                        "json",
                    ]
                )
            )["items"]
            assert len(pods) == 1
            old = pods[0]
            started = time.monotonic()
            name, uid = old["metadata"]["name"], old["metadata"]["uid"]
            remote(
                ["delete", "pod", name, "--grace-period=0", "--force", "--wait=false"]
            )
            while True:
                pods = json.loads(
                    remote(
                        [
                            "get",
                            "pods",
                            "-l",
                            f"app={app},campaign={args.namespace}",
                            "-o",
                            "json",
                        ]
                    )
                )["items"]
                new = next(
                    (
                        x
                        for x in pods
                        if x["metadata"]["uid"] != uid
                        and x["status"]["phase"] == "Running"
                    ),
                    None,
                )
                if new:
                    log = remote(["logs", new["metadata"]["name"], "--tail=1000"])
                    if "Registered base model" in log:
                        break
                if time.monotonic() - started > 900:
                    raise TimeoutError(f"replacement worker not registered: {app}")
                time.sleep(5)
            job = copy.deepcopy(template)
            job["metadata"]["name"] = f"pd-recovery-pairs-{index}"
            remote(["apply", "-f", "-"], json.dumps(job))
            pairs = wait_job(job["metadata"]["name"], 1000)
            report["faults"].append(
                {
                    "app": app,
                    "old_pod": name,
                    "old_uid": uid,
                    "new_pod": new["metadata"]["name"],
                    "new_uid": new["metadata"]["uid"],
                    "seconds": time.monotonic() - started,
                    "pairs": pairs,
                }
            )
            print(json.dumps({"fault": index, "passed": True}), flush=True)
        report["passed"] = True
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
