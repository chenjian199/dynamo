<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 升级目标

每次升级一个 YAML，引用官方稳定版本与来源，固定 B/H/T 引用、官方 target commit、逐平台镜像/架构/vLLM 目标、静态检查及回归预算。先通过 `upgrade validate-plan`，再由 `upgrade init` 解析完整身份并冻结。

[dynamo-1.6.2.yaml](dynamo-1.6.2.yaml) 是本次待执行计划：CUDA vLLM 0.31.0、Ascend 0.30.0、MetaX 0.28.0。它没有虚构未观察的厂商镜像摘要，也没有改写旧的已验证基线。镜像实际包版本由 probe-image 记录，在候选源码中审查后更新 `container/platforms/*.json`。

下次升级新增目标文件，不覆盖已经冻结的会话。`HEAD` 只用于 init 时选择含工作流的当前 HEYI 提交；lock 中保存的是完整 SHA，不会随分支移动。

[dynamo-1.6.2-ascend.yaml](dynamo-1.6.2-ascend.yaml) 按用户 2026-11-11 的安排优先执行华为全套升级；功能必测项、对比阈值保持不变。CUDA 和 MetaX 多机回归延期，不属于本会话的已认证范围，原三平台目标仍保留用于后续推进。
