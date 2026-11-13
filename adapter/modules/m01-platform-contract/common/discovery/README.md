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

版本从源码中的 `container/platforms/<platform>.json` 读取，并逐项精确比较发现记录中的包版本；不使用历史版本常量。导出源码没有 `.git` 时，必须提供包含 commit 和 files 摘要的 `SOURCE.json`，脚本核对所有使用的源码及版本锁。此源码身份只说明测试入口来源，不能替代运行镜像及安装包的身份验收。
