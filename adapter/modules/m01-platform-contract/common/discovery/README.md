<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 平台发现验证

| 文件 | 作用 |
| --- | --- |
| [platform-smoke.py](platform-smoke.py) | 面向已启动的 KV workers，检查发现元数据以及不满足平台约束时的启动拒绝；当前参数支持 MetaX / Ascend |
| [metax-platform.md](metax-platform.md) | MetaX 平台契约、实现与既有验证过程说明 |

先通过 [平台 bringup](../../../../platforms/README.md) 准备服务，再查看脚本 `--help`，明确 namespace、发现目录、目标平台和输出。其他平台按所选 feature 的入口执行，不能由本脚本覆盖范围推断完整 M01 支持。

脚本不分发 SSH 密钥，也不安装驱动。验收要求见 [M01](../../README.md)，新结果存入 [results](../../../../results/README.md)。
