<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M09 工具与依赖准备

| 文件 | 作用 |
| --- | --- |
| [package.py](package.py) | 打包待测 Python 源码 overlay 并保存逐文件摘要 |
| [requirements-planner-cuda.txt](requirements-planner-cuda.txt)、[requirements-planner-ascend.txt](requirements-planner-ascend.txt) | 对应平台 Planner 测试工具依赖；不是引擎版本的另一份锁 |
| [patch-aiperf-local-fast.py](patch-aiperf-local-fast.py) | 在隔离 tools overlay 中处理显式指定的本地 fast tokenizer |
| [run-unit.sh](run-unit.sh)、[pytest.ini](pytest.ini) | M09 相关单元检查入口与配置 |
| [reproduce.md](reproduce.md) | 既有回归的复现步骤 |
| [tp-delegation.md](tp-delegation.md) | TP 参数与原生引擎能力边界说明 |

开发 overlay 证据与最终安装镜像证据需区分；最终镜像回归由 [M10](../../../m10-validation/README.md) 组织。引擎版本读取 [container/platforms](../../../../../container/platforms)，后续流程见 [M09 工具索引](../README.md)。
