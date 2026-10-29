<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 平台开发与启动工具

此目录存放跨模块使用的平台开发环境、启动脚本及早期基础验收记录，不能整体视为 M01。

| 平台 | 入口 | 用途 |
| --- | --- | --- |
| MetaX | [环境说明](metax/metax-bringup.md)、[开发容器](metax/metax-dev.sh) | 官方镜像、挂载源码、editable 安装、基础推理与 KV 路由 |
| Ascend | [环境说明](ascend/ascend-bringup.md)、[开发容器](ascend/ascend-dev.sh) | 华为环境准备、基础推理、平台契约及 KV 路由 |
| CUDA | [开发容器](cuda/cuda-dev.sh) | NVIDIA 开发环境与源码挂载；验收记录位于各模块 |

M01 专属验证位于 [m01](../m01/README.md)。可交付 runtime/dev 镜像构建位于 [M02](../m02/README.md) 与 [container/platforms](../../container/platforms)，引擎功能验收位于 [M03](../m03/README.md)。本目录脚本中的路径按仓库根目录解析；仅移动目录，不改变厂商版本基线。
