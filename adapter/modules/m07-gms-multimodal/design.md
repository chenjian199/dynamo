<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M07 标准适配设计

将 GMS、媒体传输、编码器拆分和 Omni 阶段能力拆开判断。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 验证厂商实际提供的 VMM 导出/导入和跨进程生命周期；缺少必要公开接口时保留阻塞依据，不伪装成等价服务。
2. 文本、图像、视频及媒体搬运按实际资产回归；codec 限制和硬件传输限制分别记录。
3. 独立 encoder/Omni stage 需要实际 hidden-state 通道；聚合进程成功不证明阶段间传输已实现。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M07-F01](features/f01-gms/feature.yaml) | VMM 句柄共享与 GMS | ssh, workspace, device, runtime, images |
| [M07-F02](features/f02-media/feature.yaml) | 多模态输入与编码 | ssh, workspace, device, runtime, images, models |
| [M07-F03](features/f03-encoder/feature.yaml) | 独立编码器传输 | ssh, workspace, device, runtime, images, models |
| [M07-F04](features/f04-omni/feature.yaml) | Omni 阶段生命周期与通道 | ssh, workspace, device, runtime, images, models |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
