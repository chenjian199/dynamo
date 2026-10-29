<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M01 平台契约与能力注册

本目录归档 M01 的验证脚本与历史验收记录。平台实现仍位于 [Python platform 包](../../components/src/dynamo/common/platform) 和 [Rust platform crate](../../lib/platform)，详细设计见 [M01 设计](../modules/m01-platform-contract/README.md)。

M01 负责平台身份、设备描述、能力探测、启动前校验，以及 Python → Rust → 服务发现的信息传递。它不负责镜像构建、推理算法或 KV 数据传输；这些分别属于 M02、M03、M05/M06。普通引擎的 TP/PP 参数由原生引擎决定，PD/KVBM 等组合能力按自身条件检查。

## 文件与证据

| 入口 | 内容 |
| --- | --- |
| [platform-smoke.py](platform-smoke.py) | MetaX / Ascend 历史平台契约验证：发现身份、幂等配置、能力拒绝；需要正在运行的开发环境与服务 |
| [metax-platform.md](metax-platform.md) | MetaX M01 实现与复现说明 |
| [results/metax](results/metax) | MetaX 平台契约及基础推理、KV 路由回归结果 |
| [results/ascend](results/ascend) | Ascend 平台契约及同期 MetaX 回归结果 |
| [M03 三平台验收](../m03/README.md) | 包括 CUDA 平台接入与引擎回归；原始证据保留在所属模块 |
| [集群回归方案与结果](../m02/cluster-regression-plan.md) | 后续 CUDA 0.28.0 / Ascend 0.23.0 集群复验与限制 |

第一版平台契约已完成三平台当前配置验收，不等于所有后续能力均已认证。版本以 [版本基线](../version-baselines.md) 和每份结果的运行环境为准；早期 CUDA 结果中的旧版本不会被改写。

## 与平台工具的关系

原 `adapt/ascend`、`adapt/metax`、`adapt/cuda` 混合了容器准备、基础推理和 KV 路由工具，不全部属于 M01。通用工具现在统一放在 [platforms](../platforms/README.md)，M01 专属脚本和证据迁到本目录；M02–M08 的模块目录保持独立。

路径迁移没有重新运行硬件测试。历史结果中的命令、源文件路径及哈希保留原始值；新命令使用 `adapter/`。
