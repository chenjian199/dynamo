<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Operator 制作与部署准备

| 文件 | 作用 |
| --- | --- |
| [build-operator.sh](build-operator.sh) | 构建 Operator 镜像及相关交付材料 |
| [operator-image.md](operator-image.md) | 镜像制作、离线导入和安装使用步骤 |
| [operator-values-amd64.yaml](operator-values-amd64.yaml)、[operator-values-arm64.yaml](operator-values-arm64.yaml) | 不同架构的 Helm values 示例 |
| [models-nfs.yaml](models-nfs.yaml) | 模型 NFS 挂载配置示例；使用 hostPath 的现场不需要照搬 |
| [render.py](render.py) | 为 M02 整卡 runtime 镜像渲染 v1beta1 DGD |
| [check-cluster.py](check-cluster.py) | 根据渲染清单执行只读集群前置检查 |
| [helm-profiler-dra.md](helm-profiler-dra.md) | Helm 的作用、Profiler/DGDR 关系和 DRA 概念说明 |

阅读镜像文档 → 按架构构建/导入 → 核对现场 values → 安装并检查 → 进入 [cluster 回归](../cluster/README.md)。Operator 镜像不等于包含 etcd、NATS、Grove 等依赖的整套离线环境；具体依赖和已验证范围需结合交付记录。

验收标准见 [M08](../../README.md)，结果存入 [results](../../../../results/README.md)。
