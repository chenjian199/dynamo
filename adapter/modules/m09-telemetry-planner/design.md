<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M09 标准适配设计

遵循实际离线采样 → 插值画像 → 身份校验与持久化 → 在线规划的顺序。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 观测设备、请求和 KV 指标，核对资源单位与标签。
2. 固定硬件、模型、引擎参数、镜像身份和负载，保留原始采样；使用标准 Profiler 插值，不绕过 insufficient_data。
3. 验证画像加载、拒绝错配身份、复用和重启，再通过真实负载观察 Planner 的扩缩容及资源回收。
4. API 正确、在线 prediction_ready、持续预测、TTFT/TPOT 和 SLA 分开判定；一条正预测不代表持续可用。
5. 功耗写入另行声明控制范围和恢复原值，不因有只读指标而自动执行。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M09-F01](features/f01-telemetry/feature.yaml) | 指标与资源观测 | ssh, workspace, device, runtime, images, kubernetes |
| [M09-F02](features/f02-profile/feature.yaml) | 离线采样插值及画像身份 | ssh, workspace, device, runtime, images, models, kubernetes |
| [M09-F03](features/f03-planner/feature.yaml) | 在线规划扩缩容与恢复 | ssh, workspace, device, runtime, images, models, kubernetes |
| [M09-F04](features/f04-sla/feature.yaml) | SLA 与性能分析 | ssh, workspace, device, runtime, images, models, kubernetes |
| [M09-F05](features/f05-power/feature.yaml) | 功耗观测与控制 | ssh, workspace, device, runtime, images |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
