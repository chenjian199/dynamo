<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M08 公共工具

| 目录 | 负责的工作 |
| --- | --- |
| [operator](operator/README.md) | Operator 镜像制作、安装配置、基础 DGD 渲染与集群前置检查 |
| [cluster](cluster/README.md) | 已安装 Operator 的部署、准入、基础服务和跨节点回归 |
| [advanced](advanced/README.md) | Grove/KAI、DGDR、DRA 和扩展编排验证 |
| [rdma](rdma/README.md) | 跨 Pod 的设备内存传输、字节一致性及真实 RDMA 证据 |

一般先准备 Operator，再按 [M08 契约](../README.md) 选择基础、扩展或传输场景。DGDR 在此验证编排链路，完整 Profiler / Planner 能力属于 M09。各目录中的 manifests 与 delivery 文件记录既有现场，新的集群仍需明确绑定节点、镜像和模型，并将结果写到 campaign。
