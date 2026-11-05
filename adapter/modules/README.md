<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 模块索引

权威目录与依赖见 [自动生成的功能目录](../modules.md)。各模块的 README 导航到 design、execution、module.yaml、workflow.yaml 及编号功能。

## 每个模块如何阅读

| 文件 / 目录 | 内容与用途 |
| --- | --- |
| `README.md` | 本模块入口，链接功能编号与公共工具 |
| `design.md`、`background.md` | 模块设计和背景；历史阶段描述不作为当前认证结果 |
| `execution.md` | 实施与执行过程参考，复用时核对现场参数 |
| `module.yaml` | 模块编号、功能列表和产品实现路径 |
| `workflow.yaml` | 本模块工作流步骤定义 |
| `features/<功能>/README.md`、`feature.yaml` | 单个功能的说明与权威契约，含 revision、依赖、case、检查和执行入口 |
| `common/README.md`、`common/<功能组>/README.md` | 共享工具分组及逐文件用途，帮助选择合适的脚本 |

## 模块入口

| 模块 | 功能与契约 | 公共脚本说明 |
| --- | --- | --- |
| M01 平台契约 | [模块](m01-platform-contract/README.md) | [发现与平台检查](m01-platform-contract/common/README.md) |
| M02 镜像构建 | [模块](m02-build-packaging/README.md) | [runtime/dev 镜像验证](m02-build-packaging/common/README.md) |
| M03 引擎接入 | [模块](m03-engine-integration/README.md) | [引擎与高级能力](m03-engine-integration/common/README.md) |
| M04 设备内存 | [模块](m04-device-memory/README.md) | [内存检查](m04-device-memory/common/README.md) |
| M05 PD 传输 | [模块](m05-pd-transfer/README.md) | [传输、故障、补丁](m05-pd-transfer/common/README.md) |
| M06 KVBM / Collectives | [模块](m06-kvbm-collectives/README.md) | [缓存与独立调用链](m06-kvbm-collectives/common/README.md) |
| M07 GMS / 多模态 | [模块](m07-gms-multimodal/README.md) | [VMM 与多模态检查](m07-gms-multimodal/common/README.md) |
| M08 Kubernetes | [模块](m08-kubernetes/README.md) | [Operator、编排与 RDMA](m08-kubernetes/common/README.md) |
| M09 观测与规划 | [模块](m09-telemetry-planner/README.md) | [离线分析、在线规划与复核](m09-telemetry-planner/common/README.md) |
| M10 最终验证 | [模块](m10-validation/README.md) | [最终镜像回归与离线交付](m10-validation/common/README.md) |

产品实现仍在仓库的 components、lib、container 和 deploy 中；本目录组织适配契约与执行辅助。结果统一按 [results 的平台/芯片/campaign 结构](../results/README.md) 保存，不写进脚本目录。相同工具被多个功能复用时，在契约中引用其入口，不重复复制实现。
