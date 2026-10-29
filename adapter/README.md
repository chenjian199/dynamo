<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# Dynamo 多厂商生态适配

本目录用于设计和推进 Dynamo v1.5.1 的多厂商生态接入。每个推理部署选择一个厂商平台；CPU 控制面和主机内存仍可参与。目标是复用核心代码，支持 CUDA、Ascend、MetaX 及后续平台，不涉及异构推理或跨厂商混合调度。

源码基线：`v1.5.1`，提交 `2093662a4d4e5fd318732935213dde08dc7be818`。

当前阶段：M01–M07 已完成各自记录范围内的适配与回归，保留明确暂缓项及原生引擎问题；不能将其视为全部设计能力完成。CUDA / Ascend 的大小模型、跨节点与 DeepSeek 验证见 [集群回归记录](m02/cluster-regression-plan.md)。M08 已准备 Operator 适配及离线交付材料，后续先做 M08/M09 适配与集群验证，再构建新镜像完成 M10 最终回归。

## 目录约定

- `platforms/{cuda,ascend,metax}`：跨模块开发环境、启动工具及早期推理记录，见 [平台工具](platforms/README.md)。
- `m01`：平台契约专属脚本与结果；`m02`–`m08`：对应模块实现配套工具与验收记录。
- `modules/m01-*`–`modules/m10-*`：各模块设计；尚未建立执行目录的模块不代表已完成。
- `results` 子目录：历史证据，原始命令和哈希不会因目录改名而改写。

原 `adapt/` 已统一改名为 `adapter/`。请从仓库根目录执行更新后的命令；远端旧副本需要重新同步才会使用新路径。

## 阅读入口

| 文件 | 用途 |
| --- | --- |
| [m01/README.md](m01/README.md) | M01 平台契约、脚本和三平台证据入口 |
| [workload.md](workload.md) | v1.5.1 至当前工作树的累计工作量、统计口径及复现命令 |
| [design.md](design.md) | 范围、架构边界、接口职责、构建策略、准备工作和实施关卡 |
| [modules.md](modules.md) | 模块编号、源码位置、工作包、依赖关系与后续细化模板 |
| [modules/README.md](modules/README.md) | 十个模块详情入口、公共契约归属及逐文件修改/新增计划 |
| [support-matrix.md](support-matrix.md) | 现状、能力状态、版本组合与验收证据记录规则 |
| [metax-bringup.md](platforms/metax/metax-bringup.md) | MetaX 实测版本、兼容修复、容器构建/启动/验收命令及限制 |
| [metax-platform.md](m01/metax-platform.md) | M01 实现、兼容边界、平台身份和拒绝启动的验收证据 |
| [ascend-bringup.md](platforms/ascend/ascend-bringup.md) | 华为 0.23.0 镜像、ARM 构建、平台身份、基础推理及 KV 验收 |
| [metax-kv-routing.md](platforms/metax/metax-kv-routing.md) | MetaX 双 Worker KV 路由、缓存失效修复及真实事件验收 |

## 架构范围

架构分层见 [总体设计](design.md)。每个部署只选择一个加速平台。NIXL 等传输库以及厂商 SDK 是插件可使用的实现，不是所有平台必须共同安装的依赖。

## 后续工作

按 [模块状态](modules.md) 推进 M08/M09，再以新镜像完成 M10；具体厂商限制见 [支持矩阵](support-matrix.md)。未验证的能力不标记为已支持，关闭某项功能不等于完成该项适配。
