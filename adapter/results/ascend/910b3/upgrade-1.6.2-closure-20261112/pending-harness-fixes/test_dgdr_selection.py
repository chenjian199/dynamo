# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).parent / "adapter/modules/m08-kubernetes/common/advanced/check-dgdr.py"
spec = importlib.util.spec_from_file_location("dgdr_check", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    components = [
        {"name": "Frontend", "type": "frontend"},
        {"name": "decode", "type": "worker", "podTemplate": {"spec": {"containers": [
            {"name": "main", "args": ["--disaggregation-mode", "decode"]}
        ]}}},
        {"name": "prefill", "type": "worker", "podTemplate": {"spec": {"containers": [
            {"name": "main", "args": ["--disaggregation-mode=prefill"]}
        ]}}},
    ]
    return {"spec": {"components": components}, "status": {"components": {
        "decode": {"runtimeNamespace": "owned", "replicas": 2},
        "prefill": {"runtimeNamespace": "owned", "replicas": 1},
    }}}


class SelectionTests(unittest.TestCase):
    def test_generated_names(self):
        d = fixture()
        decode, prefill = module.inference_components(d)
        self.assertIs(decode, d["status"]["components"]["decode"])
        self.assertIs(prefill, d["status"]["components"]["prefill"])

    def test_old_service_shape(self):
        d = fixture()
        d["spec"] = {"services": {
            "VllmDecodeWorker": {"componentType": "worker", "subComponentType": "decode"},
            "VllmPrefillWorker": {"componentType": "worker", "extraPodSpec": {"mainContainer": {"args": ["--is-prefill-worker"]}}},
        }}
        d["status"]["components"] = {"VllmDecodeWorker": d["status"]["components"]["decode"], "VllmPrefillWorker": d["status"]["components"]["prefill"]}
        decode, prefill = module.inference_components(d)
        self.assertEqual((decode["replicas"], prefill["replicas"]), (2, 1))

    def test_missing_decode_or_ambiguous_roles(self):
        for kind in ("absent", "duplicate"):
            d = fixture()
            if kind == "absent":
                d["spec"]["components"].pop(1)
            else:
                other = copy.deepcopy(d["spec"]["components"][1])
                other["name"] = "second-decoder"
                d["spec"]["components"].append(other)
            with self.assertRaises(ValueError):
                module.inference_components(d)

    def test_runtime_mismatch_and_absent_status(self):
        d = fixture()
        d["status"]["components"]["prefill"]["runtimeNamespace"] = "other"
        with self.assertRaises(ValueError):
            module.inference_components(d)
        d = fixture()
        del d["status"]["components"]["decode"]
        with self.assertRaises(KeyError):
            module.inference_components(d)

    def test_aggregated(self):
        d = fixture()
        d["spec"]["components"] = [{"name": "aggregate", "type": "worker"}]
        d["status"]["components"]["aggregate"] = d["status"]["components"]["decode"]
        decode, prefill = module.inference_components(d)
        self.assertEqual(decode["replicas"], 2)
        self.assertIsNone(prefill)


if __name__ == "__main__":
    unittest.main()
