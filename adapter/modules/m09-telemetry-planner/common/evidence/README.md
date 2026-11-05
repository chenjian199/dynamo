<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# M09 证据复核

| 文件 | 作用 |
| --- | --- |
| [check_profile_exports.py](check_profile_exports.py) | 检查 Profiler 候选回执及测量导出完整性 |
| [check_offline_profile.py](check_offline_profile.py) | 验证在没有实时 FPM 的情况下，文件画像能初始化预测模型 |
| [check_online_planning.py](check_online_planning.py) | 要求在线过程实际加载画像并产生 P/D 规划，而不仅是部署健康 |
| [summarize.py](summarize.py)、[summarize_offline.py](summarize_offline.py) | 汇总保留记录，不把缺失阶段、失败或延迟未达标写成通过 |
| [test_online_planning.py](test_online_planning.py) | 防止 Planner 未生效但服务健康的场景被错误认证 |

这些工具读取 [offline](../offline/README.md) 与 [online](../online/README.md) 产生的材料。它们输出的复核结果仍需由统一 [工作流证据门禁](../../../../workflow/evidence/README.md) 绑定功能契约与本次冻结身份。

本组工具偏向功能与材料完整性检查，不自动构成生产 SLA 认证或跨平台公平性能对比。
