<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M04 标准适配设计

建立设备内存与同步的通用语义，保持和 CUDA 基准相同的职责边界。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 核对上下文、设备归属和分配/注册/释放路径，拒绝错误设备及失效句柄。
2. 验证 host/device roundtrip、流和事件顺序，以及取消后的生命周期。
3. 记录真实搬运路径；未实现直接 P2P 时不能把 host staging 写成直接 P2P。只要求声明范围内与 CUDA 基准对应的能力。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M04-F01](features/f01-memory/feature.yaml) | 设备内存分配注册和复制 | ssh, workspace, device, runtime, images |
| [M04-F02](features/f02-synchronization/feature.yaml) | 流事件与所有权回收 | ssh, workspace, device, runtime, images |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
