<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 目录、矩阵与定版审计

本包将权威契约和结果记录整理为可阅读的视图，不执行硬件测试。

| 文件 | 作用 |
| --- | --- |
| [reports.py](reports.py) | 生成模块功能目录、平台支持矩阵和版本基线，区分历史证据、当前认证、时效与暂缓范围 |
| [audit.py](audit.py) | 检查工作流定版清单中的必需项、证据路径及剩余工作 |

在仓库根目录运行：

```bash
python3 -m adapter.workflow report --catalog
python3 -m adapter.workflow report --catalog --check
```

第一条生成 [modules.md](../../modules.md)、[support-matrix.md](../../support-matrix.md) 和 [version-baselines.md](../../version-baselines.md)；第二条只检查是否过期。修改契约、平台配置或结果后重新生成，不手工编辑这些视图。

定版清单见 [workflow-audit.yaml](../../contracts/workflow-audit.yaml)。工作流 `ready` 与具体平台功能 `verified` 是不同结论；暂缓、未测或证据过期不能显示为实测通过。
