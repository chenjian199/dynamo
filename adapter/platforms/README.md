<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 平台开发与启动工具

此目录存放平台版本锁引用及跨模块使用的开发环境、启动脚本，不能整体视为 M01。

| 平台 | 入口 | 用途 |
| --- | --- | --- |
| [MetaX](metax/README.md) | [开发工具说明](metax/bringup/README.md)、[集群入口](metax/cluster/README.md) | 官方镜像、挂载源码、editable 安装、基础推理与 KV 路由 |
| [Ascend](ascend/README.md) | [开发工具说明](ascend/bringup/README.md) | 华为环境准备、基础推理、平台契约及 KV 路由 |
| [CUDA](cuda/README.md) | [开发工具说明](cuda/bringup/README.md) | NVIDIA 开发环境与源码挂载；验收记录统一位于 results |

M01 专属验证位于 [m01](../modules/m01-platform-contract/execution.md)。可交付 runtime/dev 镜像构建位于 [M02](../modules/m02-build-packaging/execution.md) 与 [container/platforms](../../container/platforms)，引擎功能验收位于 [M03](../modules/m03-engine-integration/execution.md)。本目录脚本中的路径按仓库根目录解析；仅移动目录，不改变厂商版本基线。

各平台 `platform.yaml` 引用 `container/platforms` 中的唯一版本锁。新环境清单从 `adapter/environments/examples/` 建立，先执行工作流 doctor；本目录的现场脚本不能代替环境预检。历史基础验收记录统一迁移到 `adapter/results/<平台>/<芯片>/<campaign>/features/<功能编号>/`。
