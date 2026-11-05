<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M09 公共工具

| 目录 | 负责的工作 |
| --- | --- |
| [packaging](packaging/README.md) | 工具依赖、源码测试 overlay 与单元检查 |
| [rendering](rendering/README.md) | 小/大模型、指标、Profiler 和离线采样的部署清单 |
| [offline](offline/README.md) | 真实采样、插值、画像保存、身份记录与重启复用 |
| [online](online/README.md) | 有界负载、真实 Planner 扩缩容和恢复观测 |
| [evidence](evidence/README.md) | 独立核对离线画像、在线决策、性能测量及汇总 |

完整流程是准备工具与清单 → 离线采样/插值并保存画像 → 在线 Planner 加载画像 → 负载与恢复回归 → 独立核对证据。仅有服务可用或副本变化，不能证明已经完成离线画像驱动的规划。

功能契约见 [M09](../README.md)。复用已有脚本时需重新绑定版本、模型、拓扑和预算，新结果写入 campaign；生产 SLA 与本轮有界功能检查的结论分开。
