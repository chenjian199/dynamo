# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise installers on exact vendor sources and the vendor config filtering statements."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

source, evidence = map(Path, sys.argv[1:])
transfer = source / 'adapter/modules/m05-pd-transfer/common/transfer'
raw = json.loads((evidence / 'vendor-api-sources.json').read_text())
manifest = json.loads((transfer / 'patches/ascend-0.30.0/ascend-tp-manifest.json').read_text())
modules = []
for name in ('patch_vendor', 'patch_ascend_tp'):
    spec = importlib.util.spec_from_file_location(name, transfer / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    modules.append(module)
result = {'cases': [], 'source_commit': 'uncommitted vendor patch candidate', 'passed': False}
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    initial = {}
    for relative in manifest:
        data = ((evidence / 'vendor-ascend-config-030.py').read_text() if relative == 'ascend_config.py'
                else raw['vllm_ascend/' + relative]['content'])
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        initial[relative] = data
    def invoke(module, action, version='0.30.0'):
        argv = ['test', action] + (['--platform', 'ascend'] if module is modules[0] else [])
        with patch.object(sys, 'argv', argv), patch.object(module.importlib.metadata, 'version', return_value=version), patch.object(module.importlib.util, 'find_spec', return_value=SimpleNamespace(origin=str(root/'__init__.py'), submodule_search_locations=[str(root)])), contextlib.redirect_stdout(io.StringIO()):
            module.main()
    invoke(modules[0], '--apply')
    invoke(modules[1], '--apply')
    for relative, item in manifest.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == item['after']
    result['cases'].append('all output hashes match')
    invoke(modules[1], '--apply')
    result['cases'].append('reapply is idempotent')
    tree = ast.parse((root / 'ascend_config.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'init_ascend_config')
    statements = [n for n in function.body if isinstance(n, ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('_NON_USER_INPUT_KEYS', 'kwargs', 'unknown_keys')]
    assert len(statements) == 3
    additional = {'enable_dsa_cp': True, 'consolidator_endpoints': ['a','b','c'], 'benchmark': {'mode':'prefill'}, 'dynamo_kv_event_block_size': 128, 'misspelled_vendor_option': True}
    original = dict(additional)
    scope = {'additional_config': additional, 'AscendConfig': SimpleNamespace(__dataclass_fields__={'enable_dsa_cp': None})}
    exec(compile(ast.Module(body=statements,type_ignores=[]), 'vendor-filter', 'exec'), scope)
    assert scope['kwargs'] == {'enable_dsa_cp': True, 'misspelled_vendor_option': True}
    assert scope['unknown_keys'] == ['misspelled_vendor_option']
    assert additional == original
    result['cases'].append('Dynamo metadata retained; native option retained; unknown native option still rejected by validation boundary')
    invoke(modules[1], '--restore')
    invoke(modules[0], '--restore')
    assert all((root / p).read_text() == text for p, text in initial.items())
    result['cases'].append('restore exactly recovers all vendor bytes')
    invoke(modules[0], '--apply')
    target = root/'ascend_config.py'; target.write_text(target.read_text()+'# unknown mutation\n')
    before = {p:(root/p).read_bytes() for p in manifest}
    try:
        invoke(modules[1], '--apply')
    except RuntimeError as error:
        assert 'Unknown source' in str(error)
    else:
        raise AssertionError('Unknown source accepted')
    assert all((root/p).read_bytes()==data for p,data in before.items())
    result['cases'].append('unknown source rejected without partial writes')
    try:
        invoke(modules[1], '--apply', '0.31.0')
    except RuntimeError as error:
        assert 'Unsupported Ascend' in str(error)
    else:
        raise AssertionError('Unknown version accepted')
    result['cases'].append('unknown vendor version rejected')
result['passed'] = True
print(json.dumps(result, indent=2))
