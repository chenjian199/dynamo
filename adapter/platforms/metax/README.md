<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# MetaX 平台入口

| 文件 / 目录 | 作用 |
| --- | --- |
| [platform.yaml](platform.yaml) | 平台 ID、标准资源名 `metax-tech.com/gpu`、公共策略与环境模板引用 |
| [bringup/](bringup/README.md) | 开发容器、基础推理和 KV 路由调试 |
| [cluster/](cluster/README.md) | MetaX 集群部署渲染及 tools 镜像辅助入口 |

版本的唯一来源是 [container/platforms/metax.json](../../../container/platforms/metax.json)，正式 runtime/dev 镜像使用 [M02](../../modules/m02-build-packaging/README.md) 流程。

集群入口已编写与实际集群认证是两个状态；当前适用范围以 [支持矩阵](../../support-matrix.md) 和 [问题台账](../../contracts/issues.yaml) 为准。新现场先填写 [环境清单](../../environments/README.md)，不能把缺少集群视为硬件不支持。返回 [平台索引](../README.md)。
