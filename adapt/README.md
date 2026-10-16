<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# Dynamo 多厂商生态适配

本目录用于设计和推进 Dynamo v1.5.1 的多厂商生态接入。每个推理部署选择一个厂商平台；CPU 控制面和主机内存仍可参与。目标是复用核心代码，支持 CUDA、Ascend、MetaX 及后续平台，不涉及异构推理或跨厂商混合调度。

源码基线：`v1.5.1`，提交 `2093662a4d4e5fd318732935213dde08dc7be818`。

当前阶段：已形成总体设计与 M01–M10 详细设计草案。M01 第一版公共契约已实现，其余未实现接口与路径仍属于拟议设计；平台实现、版本组合与硬件支持情况按证据分别记录。工作顺序为：总体设计 → 逐模块细化 → 按细化设计实现 → 硬件验收。

MetaX 已在官方 0.26.0 镜像内完成 C500 单卡 Qwen3-0.6B 基础文本推理，以及单机双 Worker 的真实 KV 事件路由验收，采用挂载源码与 editable 安装的开发方式；现已补齐 [M01 平台身份、能力与发现契约](metax/metax-platform.md)，不代表 M01–M10 全部完成。

## 阅读入口

| 文件 | 用途 |
| --- | --- |
| [design.md](design.md) | 范围、架构边界、接口职责、构建策略、准备工作和实施关卡 |
| [modules.md](modules.md) | 模块编号、源码位置、工作包、依赖关系与后续细化模板 |
| [modules/README.md](modules/README.md) | 十个模块详情入口、公共契约归属及逐文件修改/新增计划 |
| [support-matrix.md](support-matrix.md) | 现状、能力状态、版本组合与验收证据记录规则 |
| [metax-bringup.md](metax/metax-bringup.md) | MetaX 实测版本、兼容修复、容器构建/启动/验收命令及限制 |
| [metax-platform.md](metax/metax-platform.md) | M01 实现、兼容边界、平台身份和拒绝启动的验收证据 |
| [metax-kv-routing.md](metax/metax-kv-routing.md) | MetaX 双 Worker KV 路由、缓存失效修复及真实事件验收 |

## 架构范围

架构分层见 [总体设计](design.md)。每个部署只选择一个加速平台。NIXL 等传输库以及厂商 SDK 是插件可使用的实现，不是所有平台必须共同安装的依赖。

## 下一轮细化

从 [M01 平台契约](modules/m01-platform-contract/README.md) 和 [M02 构建与依赖隔离](modules/m02-build-packaging/README.md) 的接口和文件计划开始收敛，并核实 [M03 引擎](modules/m03-engine-integration/README.md) 的软件栈组合。随后依据模块依赖推进实现和验证。

每个模块细化后都应明确源码入口、接口语义、平台支持证据、测试条件和交付范围。未验证的厂商能力不得标记为已支持；关闭某项功能不等于完成该项适配。
