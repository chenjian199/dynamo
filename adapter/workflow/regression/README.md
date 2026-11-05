<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 跨模块回归工具

此处保存多个模块共同使用的回归工具，避免把集群渲染、请求客户端和结果采集分别复制到每个 M01–M10 模块。

目前入口为 [cluster](cluster/README.md)：包含集群清单渲染、模型资产检查、文本与多模态客户端、PD / KVBM 测试、原生引擎对照和资源采集。具体功能的验收要求仍由 [模块契约](../../modules/README.md) 定义。

这些脚本包含先前现场的默认路径和命名。新现场需在 case binding / recipe 中明确节点、模型、镜像和输出目录，不应直接把默认值当成当前环境。先通过 `doctor`，再按选定契约调用，结果写入所属 [campaign](../../results/README.md)。
