<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M02 标准适配设计

从厂商固定镜像增量构建，确保 runtime、dev 和工具环境具备可追溯关系。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 建立最小开发环境和 editable 安装，检查导入位置及 Rust 绑定身份。
2. 读取 container/platforms 版本锁，保护厂商 vLLM/Torch/传输包；CPU Profiler 依赖冲突通过独立环境解决。
3. 构建 runtime，再派生 dev/tools；记录源码、工具链、依赖及 digest。复用原生 CUDA 构建入口，不复制另一套从头构建流程。
4. 离线缓存、镜像层完整性和重现安装交给 M10 交付回归；构建成功不等于设备验收通过。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M02-F01](features/f01-bootstrap/feature.yaml) | 开发环境与 editable 安装 | ssh, workspace, runtime, images |
| [M02-F02](features/f02-build/feature.yaml) | Rust Python 依赖隔离构建 | ssh, workspace, runtime, images |
| [M02-F03](features/f03-images/feature.yaml) | runtime dev tools 镜像 | ssh, workspace, runtime, images |
| [M02-F04](features/f04-offline/feature.yaml) | 离线构建与版本保护 | ssh, workspace, runtime, images |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
