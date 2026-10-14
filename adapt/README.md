<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Dynamo 多厂商生态适配

本目录用于设计和推进 Dynamo v1.5.1 的多厂商生态接入。每个推理部署选择一个厂商平台；CPU 控制面和主机内存仍可参与。目标是复用核心代码，支持 CUDA、Ascend、MetaX 及后续平台，不涉及异构推理或跨厂商混合调度。

源码基线：`v1.5.1`，提交 `2093662a4d4e5fd318732935213dde08dc7be818`。

当前阶段：已形成总体设计与 M01–M10 详细设计草案。接口名称、配置和新增源码路径属于拟议设计；平台实现、版本组合与硬件支持情况按证据分别记录。工作顺序为：总体设计 → 逐模块细化 → 按细化设计实现 → 硬件验收。

## 阅读入口

| 文件 | 用途 |
| --- | --- |
| [design.md](design.md) | 范围、架构边界、接口职责、构建策略、准备工作和实施关卡 |
| [architecture.svg](architecture.svg) | 可直接打开的中文适配架构图；SVG 本身是可编辑源文件 |
| [modules.md](modules.md) | 模块编号、源码位置、工作包、依赖关系与后续细化模板 |
| [modules/README.md](modules/README.md) | 十个模块详情入口、公共契约归属及逐文件修改/新增计划 |
| [support-matrix.md](support-matrix.md) | 现状、能力状态、版本组合与验收证据记录规则 |

## 架构图

![Dynamo 单厂商部署的多生态适配架构](architecture.svg)

图中底部为可选择的平台适配包集合，每个部署只选择其中一个。NIXL 等传输库以及厂商 SDK 是插件可使用的实现，不是所有平台必须共同安装的依赖。

## 下一轮细化

从 [M01 平台契约](modules/m01-platform-contract/README.md) 和 [M02 构建与依赖隔离](modules/m02-build-packaging/README.md) 的接口和文件计划开始收敛，并核实 [M03 引擎](modules/m03-engine-integration/README.md) 的软件栈组合。随后依据模块依赖推进实现和验证。

每个模块细化后都应明确源码入口、接口语义、平台支持证据、测试条件和交付范围。未验证的厂商能力不得标记为已支持；关闭某项功能不等于完成该项适配。
