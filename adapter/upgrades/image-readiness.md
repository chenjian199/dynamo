<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Dynamo 1.6.2 镜像准备状态

更新时间：2026-11-10。

| 平台 | 当前状态 | 后续动作 |
| --- | --- | --- |
| MetaX 本机 | 0.28.0 的 py310、py312 镜像及包版本已核验；计划选择 py310 | 在升级适配阶段使用计划内镜像开展验证 |
| Ascend 华为 52 | Docker 中的 v0.30.0 ARM64 镜像及包版本已核验；CRI 中未找到目标标签 | 后续集群部署前导入对应镜像存储，并开展适配验证 |
| CUDA H800 4.6 | 用户确认镜像仍在下载；按用户要求暂缓 CUDA 回归 | 下载完成后核验镜像 ID、RepoDigest、架构及实际包版本，再补 CUDA 回归 |

用户本次确认：“4.6还在下载，可以先不执行cuda的回归测试”。这是测试顺序调整，不是取消 CUDA 支持，也不是豁免最终回归。

此前 [现场检查记录](../results/workflow/upstream/image-preparation-20261110-145356/README.md)及其校验清单保持原样；本页补充用户后续提供的下载状态，不将其伪装成重新执行的镜像探测。

镜像元数据通过不代表推理验收通过。CUDA 回执和回归证据补齐前，不宣称三平台升级完成，不降低既有升级门禁，也不修改已验证的平台版本锁。
