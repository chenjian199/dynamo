<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M05 标准适配设计

协议布局、实际传输与故障回收分别验收。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 先验证原生 connector，再检查 Dynamo 交接元数据；厂商补丁必须有版本和 SHA256 保护。
2. 在已选择的 NIXL 或 Mooncake 后端验证 GPU 字节一致与实际协议，不能仅凭环境变量或 API 成功证明 RDMA。
3. 至少包含 2P3D、秩映射与异宽 TP；引擎原生支持的组合可启动，认证只覆盖实测组合。
4. 分别记录取消、故障恢复、30 分钟且至少 1000 请求的有界长稳及资源趋势。底层 abort 不能用上层重新拉起冒充底层已修复。
5. Hybrid 布局保留每组 block IDs、步长与映射；不套用普通 Connector 的块尺寸假设。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M05-F01](features/f01-contract/feature.yaml) | PD 布局及交接协议 | ssh, workspace, device, runtime, images, models |
| [M05-F02](features/f02-transport/feature.yaml) | NIXL Mooncake 传输 | ssh, workspace, device, runtime, images, models |
| [M05-F03](features/f03-unequal/feature.yaml) | 不等数量 P D | ssh, workspace, device, runtime, images, models |
| [M05-F04](features/f04-asymmetric/feature.yaml) | 异宽 TP 与秩映射 | ssh, workspace, device, runtime, images, models |
| [M05-F05](features/f05-crossnode/feature.yaml) | 跨节点 KV 传输 | ssh, workspace, device, runtime, images, models, kubernetes, multinode, network |
| [M05-F06](features/f06-recovery/feature.yaml) | 故障取消回收长稳 | ssh, workspace, device, runtime, images, models |
| [M05-F07](features/f07-hybrid/feature.yaml) | 混合 KV 布局 | ssh, workspace, device, runtime, images, models |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
