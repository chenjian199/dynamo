<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 最终镜像的分析与规划回归

| 文件 / 分组 | 作用 |
| --- | --- |
| [render-offline.py](render-offline.py) | 将 M09 有界采样绑定到最终镜像和新的测试身份 |
| [render-services.py](render-services.py) | 生成无产品源码覆盖的 DGDR、容量、指标和 CPU 负载服务清单 |
| [offline-profile.py](offline-profile.py)、[run-offline-case.sh](run-offline-case.sh) | 通过已安装产品 API 测量、持久化和组装画像，运行大模型采样场景 |
| [audit-profile.py](audit-profile.py) | 检查采样和导出完整性，不修改原始测量，也不认证生产 SLO |
| [expand-decode-profile.py](expand-decode-profile.py) | 冻结新的宽 batch 采样输入，保留原失败采样 |
| [prepare-online.py](prepare-online.py) | 将最终镜像测得的画像接入在线部署 |
| [restart-profile.py](restart-profile.py) | 重启测试 Planner 并检查复用相同画像 |
| [recheck-online.sh](recheck-online.sh) | 既有工作目录中的指标修复后在线复查 |
| [verify-offline-budget.py](verify-offline-budget.py) | 核对并修正继承的 GPU 预算元数据，不改变已测量引擎输入 |

流程是渲染采样 → 实测与审计 → 加载画像 → 在线决策/重启检查。仅画像文件存在或 API 可用，不能证明预测器持续正常。新采样配置使用新的身份和输出目录；完整流程见 [M10 公共工具](../README.md)。

渲染时必须提供 `--runtime-version`（例如 `--runtime-version 1.6.2`），取值为实际安装镜像中的 Dynamo 版本。该参数同时替换历史 DGD 组件及 DGDR 的 `runtimeVersionOverride`，不能只更新镜像引用而保留旧运行时协议版本。

`prepare-online.py` 也要求显式 `--runtime-version`，避免生成在线 Planner 时重新写入旧协议版本。两个历史集群复查 shell 通过必填 `M10_RUNTIME_VERSION` 传递该值；这些脚本的节点、目录和 namespace 仍须按实际 campaign 绑定。
