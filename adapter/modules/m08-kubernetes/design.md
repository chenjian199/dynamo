<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M08 标准适配设计

使用标准厂商资源名和现有 Operator API，认证 Kubernetes 编排链路。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 先检查设备插件、节点 allocatable、RBAC 和模型挂载，逐项验证 admission 与 Pod 生成。
2. 认证 Operator 跨节点 PD 的实际放置及传输，再检查 Grove/KAI 的调度、扩缩容和资源回收。
3. DGDR 在此模块验证分析 Job、结果消费与部署状态；采样质量和规划效果属于 M09。
4. DRA 为独立可选路径，必须记录 Kubernetes API、驱动及 ResourceClaim 生命周期；普通 Device Plugin 成功不认证 DRA。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M08-F01](features/f01-operator/feature.yaml) | 资源名校验与 Operator 部署 | ssh, workspace, device, runtime, images, kubernetes |
| [M08-F02](features/f02-crossnode/feature.yaml) | Operator 跨节点 PD | ssh, workspace, device, runtime, images, kubernetes, multinode, network |
| [M08-F03](features/f03-grove/feature.yaml) | Grove KAI 与 Gang 调度 | ssh, workspace, device, runtime, images, kubernetes |
| [M08-F04](features/f04-dgdr/feature.yaml) | DGDR 分析任务编排 | ssh, workspace, device, runtime, images, kubernetes |
| [M08-F05](features/f05-dra/feature.yaml) | 厂商 DRA | ssh, workspace, device, runtime, images, kubernetes |
| [M08-F06](features/f06-rdma/feature.yaml) | 集群 RDMA 路径 | ssh, workspace, device, runtime, images, kubernetes, multinode, network |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
