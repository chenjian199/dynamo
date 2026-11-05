<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M01 标准适配设计

先贯通平台识别，再证明设备身份从 Python 传递到 Rust 服务发现后仍一致。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 明确平台选择与自动识别优先级，未知平台及未知能力不能默认为 CUDA。
2. 在可见设备过滤前后核对逻辑序号、物理设备和服务发现记录；不要把 CUDA API 外观当作 NVIDIA 硬件身份。
3. 采集实际厂商版本、驱动和设备能力；保持 CPU、CUDA 及已有平台回归。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M01-F01](features/f01-platform/feature.yaml) | 平台身份与选择 | ssh, workspace, device |
| [M01-F02](features/f02-discovery/feature.yaml) | 设备枚举与服务发现 | ssh, workspace, device |
| [M01-F03](features/f03-stack/feature.yaml) | 版本与能力探测 | ssh, workspace, device |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
