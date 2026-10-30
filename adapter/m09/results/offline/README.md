<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# 离线画像补充验收

这是前轮实时 FPM/聚合部署动态扩缩容之外的补充，验证真实 Profiler 插值数据落盘、ConfigMap 转换、Planner 强制读取文件和重启复用。实现与复现见 [说明](../../offline-bootstrap.md)。本轮测试结束：四组通过、一组有限通过、一组未通过，总体 `passed=false`；状态以各目录原始 receipt 和 [机器汇总](summary.json) 为准。

| 用例 | P/D 样本 | 在线结果 | 请求数（负载+恢复） | 重启后复用/API |
| --- | --- | --- | --- | --- |
| CUDA Qwen3-0.6B | 6 / 6 | decode 1→2→1，prefill 1 | 908，API 错误 0 | 同指纹；HTTP 200 |
| Ascend Qwen3-0.6B | 6 / 6 | decode 峰值 2，最终 1P1D | 239，API 错误 0 | 同指纹；HTTP 200 |
| CUDA Qwen3-32B TP4 | 6 / 8 | C2 有界复用通过，在线规划仍有间歇不可用 | 139，API 错误 0 | 同指纹；HTTP 200 |
| Ascend Qwen3-32B TP4 | 6 / 8 | 8 卡预算下保持 1P1D | 31，API 错误 0 | 同指纹；HTTP 200 |
| CUDA DeepSeek-V4-Flash TP4 | 6 / 9 | C2，8 卡预算下保持 1P1D | 42，API 错误 0 | 同指纹；HTTP 200 |
| Ascend DeepSeek-V4-Flash TP8 EP | 6 / 9 | prefill 静态容量预测未通过；在线未启动 | 无在线请求；prefill 重测 96 请求无错误 | 未运行 |

本表是功能结果，不是 SLA 通过表。CUDA 小模型 C128 突发 TTFT p95 104.05 秒，恢复 247.43 ms；Ascend C16 突发 19.80 秒，恢复阶段 3.16 秒；CUDA Qwen32 C16 突发 39.44 秒，恢复 219.55 ms。高负载超过本轮目标；Ascend 恢复阶段总体也未达到 1 秒目标。大模型恰好 1P1D 的预算不允许额外副本，不能把 HOLD 当作扩容测试。

Ascend Qwen32 C2 负载 TTFT p95 1087.72 ms，恢复 388.90 ms；TPOT p95 分别为 187.43 / 176.36 ms，API 错误为零。负载 TTFT 仍超出 1000 ms 目标。其 Planner ITL 目标在负载前根据离线预测从 150 调整为 300 ms；这是 Planner 配置，与负载工具根据 16 次校准请求冻结的 261.09 ms TPOT 目标分别记录，见 `ascend-qwen32/sla-calibration.json` 和 `online-final/load.log`。

Ascend 首轮 C64 突发失败保留在 `ascend-small/failed-run.json` 指向的带哈希归档。CUDA Qwen32 的探针配置遗漏、诊断快照及修正后的 source-v9 分开记录，见 `cuda-qwen32/diagnostic-overlay.json`。CUDA DeepSeek 首轮工具 Pod OOM 后使用更大工具内存、新目录重新采集 decode，不混入失败点。

每个目录的 `profile-execution-audit.json` 区分 AIPerf warmup/profiling 阶段核验完成数；`offline-verification-*.json` 记录静态加载与确定性预测；在线目录记录 DGDSA 和 Pod；restart 目录记录前后 UID、样本数和画像指纹。完整请求和 worker 日志放在远端 `archive.json` 指向的归档中。

来源限制：源码覆盖测试不等于最终镜像验收；MetaX 无集群。CPU-only 预测重复一致不等于预测准确。画像中型号、版本、拓扑由显式输入与现场 provenance 提供，不是自动硬件认证。保留时延不达标、稀疏采样、队列压力及原生引擎问题，M10 需重新构建并复测。

补充严格在线审计：`online*/planning-audit.json` 要求正式负载期间有有效 P/D 规划。CUDA Qwen32 原 C16 轮次的 18 次负载决策和 7 次恢复决策为 `model_not_ready`，不能算在线规划通过。其 API/离线加载/重启记录保留，C2 补测 API 139 请求无错误，重启后复用相同指纹；但负载仍有 17 次 `model_not_ready`，只有 3 次有效 P/D 决策，恢复阶段 6 次有效。机器汇总标记 `passed_with_limitations`，不宣称持续稳定规划。

CUDA DeepSeek C2 的负载/恢复 TTFT p95 为 836.81 / 382.44 ms，TPOT p95 为 132.96 / 130.65 ms；时延按 streamed token IDs 计算。这轮达到校准的测试目标，不能外推为其它负载或模型质量认证。正式负载有 16 次有效 P/D 规划，恢复 5 次，均无 `model_not_ready`；校准期间测试 Prometheus 到期后的恢复记录见 `metrics-recovery-receipt.json`。

CUDA Qwen32 C2 TTFT p95 为 355.38 / 227.41 ms，TPOT p95 为 37.15 / 36.91 ms。`readiness-replay.json` 使用日志中四位小数的 FPM 重建，发现画像初始 ready，吸收在线样本后可变为 insufficient_data；这不是完整 counter/rank 精确回放，与当前 AISimulate 0.12.0 的原生保护规则一致：21 个重建样本的非负约束拟合得到两个零斜率、约 36.87 ms 常数项，上游明确拒绝这种缺少负载增长信号的模型。源码和测试定位见 `readiness-rule.json`。这解释了回放中的拒绝路径，不代表已采集完整精度的在线 rank 轨迹。脚本见 `replay_qwen_readiness.py`，原始日志在 `archive.json` 指向的 `online-bounded/planner.log`。C16 原始证据另存 `online-v9-archive.json`。

Ascend DeepSeek 完成完整 6P/9D 采样，decode 画像可重复预测（ITL 约 455.01 ms、容量约 0.27472 RPS）。prefill 初次每点 1 请求，随后固定每点 8 次预热、16 次正式请求重测全部六点，平均 TTFT 分别为 488.61、478.12、478.55、470.90、466.63、494.56 ms。两轮原生模型均为 `insufficient_data`；无 TTFT 约束、2000 ms 和 10000 ms 目标下均无容量预测。独立线性 NNLS 得到零斜率；该诊断不是完整的原生 hinge 实现。保持 AISimulate 0.12.0 原生保护，未修改测量数据或强制可用，因此在线与重启未运行，不能声称六组全部闭环。证据见 `ascend-deepseek/case-status.json`、`prefill-single-readiness.json`、`prefill-repeat-readiness.json` 和两轮 `offline-verification-*.json`。

后续应扩大 prefill 测量范围并重新固定受影响的画像身份与覆盖范围，或单独实现和验证原生 forward-pass benchmark 的离线持久化。不能用放宽 SLA、人工正斜率或复用其它平台曲线解决此问题。现有数据不证明驱动故障，也不证明预测精度。原单请求轮次存于 `single-prefill-archive.json` 指向的哈希归档，重复轮次为 `profile-v4`；复用的 decode 数据保持原字节及原画像时间戳。

资源状态：本轮 CUDA/Ascend 测试 DGD、工具 Pod 及专用 CUDA Prometheus 已退出；保留 M08 Operator 和 Ascend 共享监控。最终源码仍须在 M10 重建镜像后回归。
