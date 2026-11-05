# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression for decode canaries when aggregate flags become a PD deployment."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.gpu_0]


@pytest.mark.parametrize("existing", [False, True])
def test_decode_probe_retains_explicit_local_prefill(tmp_path, existing):
    custom = {"token_ids": [42], "annotations": ["x-bypass-remote-prefill"]}
    env = (
        [{"name": "DYN_HEALTH_CHECK_PAYLOAD", "value": json.dumps(custom)}]
        if existing
        else []
    )
    dgd = {
        "kind": "DynamoGraphDeployment",
        "metadata": {"name": "probe"},
        "spec": {
            "components": [
                {
                    "name": "VllmDecodeWorker",
                    "type": "decode",
                    "podTemplate": {"spec": {"containers": [{"env": env}]}},
                }
            ]
        },
    }
    (tmp_path / "final_config.yaml").write_text(yaml.safe_dump(dgd))
    (tmp_path / "input.json").write_text(
        json.dumps({"identity": {"platform": "cuda"}, "dgd": dgd})
    )
    output = tmp_path / "online.yaml"
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("prepare_offline_online.py")),
            "--profile",
            str(tmp_path),
            "--input",
            str(tmp_path / "input.json"),
            "--workdir",
            str(tmp_path),
            "--output",
            str(output),
        ],
        check=True,
    )
    rendered = yaml.safe_load(output.read_text())
    env = rendered["spec"]["components"][0]["podTemplate"]["spec"]["containers"][0][
        "env"
    ]
    probes = [e for e in env if e["name"] == "DYN_HEALTH_CHECK_PAYLOAD"]
    assert len(probes) == 1
    payload = json.loads(probes[0]["value"])
    assert "x-bypass-remote-prefill" in payload["annotations"]
    assert "prefill_result" not in payload
    if existing:
        assert payload == custom
