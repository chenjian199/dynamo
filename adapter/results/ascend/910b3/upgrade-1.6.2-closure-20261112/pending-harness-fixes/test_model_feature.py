# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Negative checks for model capability evidence, not actual model qualification."""
import ast
import hashlib
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

source=Path(__file__).parent/'adapter/modules/m03-engine-integration/common/engine/advanced-smoke.py'
function=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='model_feature')


class ModelEvidenceTests(unittest.TestCase):
    def run_probe(self, kind, log, dtype='I8'):
        class Header:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def keys(self):return ['weight']
            def get_slice(self,name):return SimpleNamespace(get_dtype=lambda:dtype)
        sf=ModuleType('safetensors');sf.safe_open=lambda *a,**kw:Header()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('sys.modules',{'safetensors':sf}):
            root=Path(tmp);(root/'config.json').write_text(json.dumps({'n_routed_experts':256,'num_experts_per_tok':6}));(root/'quant_model_description.json').write_text('{"weight":"W8A8"}')
            (root/'weights.safetensors').touch();(root/'engine.log').write_text(log)
            ns={'json':json,'hashlib':hashlib,'re':re,'complete':lambda *a,**kw:{'nvext':{'completion_token_ids':list(range(16))}}}
            exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),ns)
            report={};ns['model_feature'](SimpleNamespace(kind=kind,model_path=root,engine_log=root/'engine.log',expected_quantization='ascend',expected_ep=8),report)
            return report

    def test_quantization_requires_runtime_and_weight_evidence(self):
        result=self.run_probe('quant','quantization=ascend, dtype=torch.bfloat16')
        self.assertEqual(result['weight_tensor_dtypes'],{'I8':1})
        with self.assertRaises(AssertionError):self.run_probe('quant','quantization=None')
        with self.assertRaises(AssertionError):self.run_probe('quant','quantization=ascend',dtype='BF16')

    def test_moe_requires_actual_expert_ranks(self):
        group='[EP Rank 0/8] Expert parallelism is enabled. Expert placement strategy: linear. Local/global number of experts: 32/256.'
        ranks='\n'.join(f'(Worker_TP{i}_EP{i} pid={100+i}) model loaded' for i in range(8))
        result=self.run_probe('moe',group+'\n'+ranks)
        self.assertEqual(result['observed_expert_ranks'],list(range(8)))
        with self.assertRaises(AssertionError):self.run_probe('moe',group)
        with self.assertRaises(AssertionError):self.run_probe('moe',ranks)
        with self.assertRaises(AssertionError):self.run_probe('moe',(group+'\n'+ranks).replace('/256','/128'))


if __name__=='__main__':unittest.main()
