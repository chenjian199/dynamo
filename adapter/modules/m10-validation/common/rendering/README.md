<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 最终镜像部署清单

| 文件 | 作用 |
| --- | --- |
| [render.py](render.py) | 生成绑定最终镜像的回归清单，拒绝开发源码 overlay |
| [upgrade-operator.sh](upgrade-operator.sh) | 显式升级已有测试 Operator release，并在工作目录保留回滚信息 |

先完成 [镜像准备](../images/README.md)，使用实际平台、namespace 和不可变镜像身份渲染。升级脚本会变更已有测试 release，执行时需使用已确认的集群和工作目录，不能仅凭文件名判断为只读检查。

本目录与 [profiling](../profiling/README.md) 中的画像/指标清单配合，随后运行 [回归](../regression/README.md)。功能验收范围见 [M10](../../README.md)。

渲染时必须提供 `--runtime-version`（例如 `--runtime-version 1.6.2`），取值为实际安装镜像中的 Dynamo 版本。该参数同时替换历史 DGD 组件及 DGDR 的 `runtimeVersionOverride`，不能只更新镜像引用而保留旧运行时协议版本。
