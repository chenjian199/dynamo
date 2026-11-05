<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M10 标准适配设计

以最终安装镜像和明确功能范围完成交付验收。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 复用 M02 构建，核对 runtime/dev/tools/operator 的 digest、架构和版本；禁止用源码覆盖冒充最终镜像。
2. 执行所声明的跨模块小模型、大模型、单机及集群回归，保留原生对照和失败尝试。
3. 复核当前证据和继承证据的等价依据；必需项失败不允许自动降级为非必需。
4. 检查完整镜像层、离线依赖、部署文件、导入过程和 SHA256；模型、密钥及 kubeconfig 不放进包。按平台原地归档，统一索引只引用封存包。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M10-F01](features/f01-identity/feature.yaml) | 最终安装镜像身份与锁定 | ssh, workspace, runtime, images |
| [M10-F02](features/f02-regression/feature.yaml) | 跨模块最终镜像回归 | ssh, workspace, runtime, images |
| [M10-F03](features/f03-audit/feature.yaml) | 证据范围与限制审计 | ssh, workspace, runtime, images |
| [M10-F04](features/f04-offline/feature.yaml) | 离线导入与封存交付 | ssh, workspace, runtime, images |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
