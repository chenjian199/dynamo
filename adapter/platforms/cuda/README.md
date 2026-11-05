<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# CUDA 平台入口

| 文件 / 目录 | 作用 |
| --- | --- |
| [platform.yaml](platform.yaml) | 平台 ID、标准资源名 `nvidia.com/gpu`、公共策略和环境模板引用 |
| [bringup/](bringup/README.md) | NVIDIA 开发容器与源码调试环境 |

版本的唯一来源是 [container/platforms/cuda.json](../../../container/platforms/cuda.json)，不在此另维护引擎版本。正式 runtime/dev 镜像见 [M02](../../modules/m02-build-packaging/README.md)；各模块共享契约，CUDA 结果从 [支持矩阵](../../support-matrix.md) 进入。

新机器先填写 [环境清单](../../environments/README.md) 并预检。目录中有脚本不代表该机器或某种拓扑已认证。返回 [平台索引](../README.md)。
