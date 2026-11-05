<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 在线负载、扩缩容与恢复

| 文件 | 作用 |
| --- | --- |
| [campaign.py](campaign.py) | 施加有界负载并观察真实 Planner 动作，不手动设置副本数伪装扩缩容 |
| [load.py](load.py) | 流式请求、TTFT/TPOT 和固定目标测量；以 token 数而非 SSE chunk 数计算 TPOT |
| [collect.py](collect.py) | 保留完整远端归档并提取便于审计的结果 |
| [restart_smoke.py](restart_smoke.py) | 在离线画像重载回执之后验证真实 API 请求 |
| [replay_qwen_readiness.py](replay_qwen_readiness.py) | 从保留日志重建 CUDA Qwen32 readiness 现象，属于离线诊断而非新推理测试 |
| [test_load.py](test_load.py) | 检查 token 交付时间与可见文本延迟等测量语义 |

使用 [offline](../offline/README.md) 产生的匹配画像与准备好的服务，冻结请求、预算和目标后运行。服务健康、请求成功、Planner 真实决策和延迟达标是分别检查的内容；合成 token 负载不评估语义质量。

将负载、指标、资源变化和决策日志一并存入 campaign，再由 [evidence](../evidence/README.md) 复核。总体要求见 [M09](../../README.md)。
