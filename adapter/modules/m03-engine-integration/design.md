<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M03 标准适配设计

保持 Dynamo 编排接口稳定，兼容厂商引擎版本差异。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 相同模型、精度和参数下建立原生引擎对照，再验证 Dynamo 请求。
2. 覆盖流式、取消、错误传播、重启及 KV 事件；记录可见文本和 token 输出的差异。
3. TP/DP/PP/EP 参数交给引擎检查原生能力，不按已测组合设置启动白名单。
4. LoRA、量化、推测解码和 MoE 分别固定模型资产、输出判断和资源需求；资产缺失仅阻塞相关 case。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M03-F01](features/f01-baseline/feature.yaml) | 原生与 Dynamo 基础推理 | ssh, workspace, device, runtime, images, models |
| [M03-F02](features/f02-lifecycle/feature.yaml) | 流式取消与生命周期 | ssh, workspace, device, runtime, images, models |
| [M03-F03](features/f03-routing/feature.yaml) | KV 事件与路由 | ssh, workspace, device, runtime, images, models |
| [M03-F04](features/f04-parallel/feature.yaml) | TP DP PP EP 参数透传 | ssh, workspace, device, runtime, images, models |
| [M03-F05](features/f05-advanced/feature.yaml) | LoRA 量化推测解码与 MoE | ssh, workspace, device, runtime, images, models |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
