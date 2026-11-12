<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# PD 交接探针单元检查

`test_handoff.py` 使用隔离的内存客户端验证正常/错误/不完整响应、负向错误分类及交接副本的保存。它不加载模型，也不认证 GPU/NPU 或网络传输。Python 需支持 `asyncio.timeout`（3.11 及以上，与目标镜像 3.12 一致）。

从仓库根目录执行：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  -c adapter/workflow/tests/pytest.ini \
  --confcutdir=adapter/modules/m05-pd-transfer/common/transfer/tests \
  adapter/modules/m05-pd-transfer/common/transfer/tests
```
