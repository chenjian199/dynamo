<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ascend 平台入口

| 文件 / 目录 | 作用 |
| --- | --- |
| [platform.yaml](platform.yaml) | 平台 ID、标准资源名 `huawei.com/Ascend910`、公共策略和环境模板引用 |
| [bringup/](bringup/README.md) | 开发容器安装、基础推理、KV 路由与平台信息检查 |

版本的唯一来源是 [container/platforms/ascend.json](../../../container/platforms/ascend.json)。厂商插件和宿主机设备信息由实际预检记录，不因已有脚本就推定所有驱动组合可用。

正式镜像构建见 [M02](../../modules/m02-build-packaging/README.md)，跨模块功能状态见 [支持矩阵](../../support-matrix.md)，待解决或暂缓事项见 [问题台账](../../contracts/issues.yaml)。新现场从 [环境清单](../../environments/README.md) 开始。返回 [平台索引](../README.md)。
