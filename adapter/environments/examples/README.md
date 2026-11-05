<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 环境与范围模板

这些文件是待填写的示例，包含占位值，不是可以直接执行的现场配置。

| 文件 | 用途 | 需要补齐的内容 |
| --- | --- | --- |
| [local.yaml](local.yaml) | 本地源码检查的最小环境 | 实际运行时和所选用例需要的资产；默认不执行任务 |
| [cluster.yaml](cluster.yaml) | 两节点 SSH / Kubernetes 环境 | 节点、工作区、设备命令、镜像 digest、模型 manifest、context、资源名、预算 |
| [case-binding.yaml](case-binding.yaml) | 合并进现场环境的 `case_bindings` 示例 | 用例的节点、镜像、模型、拓扑、容器和各阶段命令 |
| [scope.yaml](scope.yaml) | 单独传给 `init --scope` 的范围声明 | required/optional/deferred 等范围、暂缓理由与确认记录，以及 any-of 依赖选择 |

`case-binding.yaml` 中的 `--help` 只是展示命令位置，`observe` 和 `cleanup` 也尚未填写，不能据此认证 M01。实际 run 必须调用功能契约指定的入口，observe 需产生可独立检查的测量结果。

`scope.yaml` 的 `dependency_choices` 展示 Operator 与 Grove 路径如何择一；仅在选中对应功能时声明所需选择。暂缓或不适用必须保留 reason、accepted_by 和 reference，不会被统计为通过。

文件格式见 [schemas](../../contracts/schemas/README.md)，使用方式见 [环境配置说明](../README.md)。
