<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M06 标准适配设计

分别覆盖 vLLM KVBM 接入与独立 engine/physical 调用链。

## 输入与边界

读取平台版本锁、环境预检、所选模型和拓扑以及冻结的 campaign。产品路径由 module.yaml 声明；实现复用既有 provider/backend 边界。通用状态与证据规则引用 [顶层契约](../../contracts/policy.yaml)，不在本模块重新定义。

## 实施顺序

1. 验证 host 和 SSD 卸载命中、输出正确性及路由选择；避免将缓存命中率当成端到端正确性。
2. 独立 Rust 引擎、分片搬运和 replicated MLA 广播分别建立字节及生命周期断言。
3. G4 远端缓存独立编号；没有多节点环境时暂缓，不能凭本地 G3 成功认证远端路径。

## 功能与前置条件

| 功能 | 行为 | 条件 |
| --- | --- | --- |
| [M06-F01](features/f01-tiers/feature.yaml) | G2 G3 缓存卸载命中及路由 | ssh, workspace, device, runtime, images, models |
| [M06-F02](features/f02-independent/feature.yaml) | 独立 KVBM 引擎调用 | ssh, workspace, device, runtime, images, models |
| [M06-F03](features/f03-sharded/feature.yaml) | 分片多卡搬运 | ssh, workspace, device, runtime, images, models |
| [M06-F04](features/f04-replicated/feature.yaml) | MLA replicated 广播 | ssh, workspace, device, runtime, images, models |
| [M06-F05](features/f05-remote/feature.yaml) | G4 远端缓存 | ssh, workspace, device, runtime, images, models, kubernetes |

## 输出与验收

每个功能输出独立 case 断言、命令、环境、原始结果及验证器哈希。不同平台可以使用不同厂商实现，但不能降低同一行为的验收含义。实施完成、功能通过和性能达标分别记录。失败先分类为环境、原生引擎、适配缺陷或证据不足，再决定复测；不能覆盖失败尝试。

已有工具参见 [执行说明](execution.md) 和各 feature.yaml 的 entrypoints。这些包含现场专用回归工具，节点、路径、模型和参数必须从新 campaign 重新绑定，不能直接 apply 历史清单。架构背景见 [background.md](background.md)，历史认证证据见 [支持矩阵](../../support-matrix.md)。
